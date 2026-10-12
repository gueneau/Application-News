import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..config import (
    AN_BASE,
    AN_SCRAPER_SOURCES,
    PARLEMENT_MAX_GROQ,
    STADES_ORDRE,
    STADE_PATTERNS,
    TIMEOUT,
)
from ..crawl import crawl_article
from ..filters import keyword_match
from ..llm import groq_analyse_pjl, groq_briefing_parlement
from ..supabase_sync import SupabaseSync

log = logging.getLogger(__name__)


def _is_pjl_non_environmental(titre: str) -> bool:
    """
    Pre-filter: reject PJLs with keywords indicating they are NOT environmental/climate/energy/RSE.
    Returns True if PJL should be REJECTED (non-environmental).
    """
    titre_lower = titre.lower()
    exclude_keywords = [
        # Fiscalité, impôts
        r'\bfiscal', r'\bimpôt', r'\bdouble\s+imposition', r'\bCSGI?\b',
        # Justice pénale, extradition
        r'\bextradition', r'\bjustice\b', r'\bpénal', r'\bcours?\s+pénale?',
        r'\bréadmission', r'\bcriminalité', r'\bpolice\b',
        # Diplomatie, accords internationaux génériques
        r'\baccord\s+(?!.*(?:clima|énergi|déch|carbone|environnement|RSE|CSRD|durabilité))',  # accord NOT followed by climate/energy terms
        r'\bconvention\s+(?!.*(?:climat|énergi|environnement|carbone))',  # convention NOT followed by env/climate terms
        # Sécurité sociale, retraite
        r'\bsécurité\s+social', r'\bretraite\b', r'\bpension',
        # Affaires culturelles, restitution
        r'\bculturel', r'\brestitution\s+de\s+biens', r'\bpatrimoine',
        # Commerce, douanes (générique)
        r'\bcommerce\s+(?!durabilité|carbon)', r'\bdouane(?!.*carbone)', r'\btrarifaire',
        # Agriculture (générique, sauf si trajectoire transition)
        r'\bagriculture\b(?!.*transition|durabilité)',
        # Défense, armement, sécurité militaire
        r'\bdéfense\b', r'\barmement', r'\bmilitaire',
        # Transport public (hors intérêt RSE)
        r'\btransport\s+public(?!.*véhicule|électrifi)',
    ]
    
    for keyword in exclude_keywords:
        if re.search(keyword, titre_lower):
            return True  # Reject this PJL
    
    return False  # Keep for analysis


def _detect_stade_rss(titre: str, description: str = '') -> str:
    texte = (titre + ' ' + (description or '')).lower()
    for stade, patterns in STADE_PATTERNS:
        if any(p in texte for p in patterns):
            return stade
    return ''


def _is_pjl_gouvernemental(titre: str) -> bool:
    t = (titre or '').lower().strip()
    if not t.startswith('projet de loi'):
        return False
    if any(k in t for k in ['finances', 'financement de la sécurité sociale']):
        return keyword_match(titre)
    return True


def _scrape_an_listing(source: dict, today_str: str) -> list:
    """
    Scrape a listing page and return entries:
      {titre, url_doc, url_dossier, date, source, fiche_id}
    """
    from bs4 import BeautifulSoup

    entries = []
    try:
        resp = requests.get(source['url'], timeout=TIMEOUT, headers={'User-Agent': 'ABC-Veille/2.0'})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        for item in soup.find_all(['li', 'div'], class_=re.compile(r'document|item|pjl', re.I)):
            h3 = item.find(['h3', 'h2', 'strong'])
            if not h3:
                continue
            titre = h3.get_text(strip=True)
            if not titre or len(titre) < 10:
                continue
            # Pour les textes adoptés : "Texte adopté N° 248" est le numéro, pas le titre.
            # Le vrai titre est dans un <p> de l'item, préfixé par " - ".
            if re.match(r'^Texte adopté\s+N°\s*\d+', titre, re.IGNORECASE):
                p = item.find('p')
                if p:
                    raw = p.get_text(strip=True).lstrip('- ').strip()
                    # Supprimer la date finale " le 12 décembre 2025"
                    raw = re.sub(r'\s+le\s+\d{1,2}\s+\w+\s+\d{4}\s*$', '', raw, flags=re.IGNORECASE).strip()
                    if raw and len(raw) > 10:
                        titre = raw
                    else:
                        continue
                else:
                    continue  # pas de titre réel trouvé, on skip

            date_str = today_str
            date_el = item.find(string=re.compile(r'Mis en ligne|mis en ligne', re.I))
            if date_el:
                # Chercher la date dans le texte du parent (la date peut être dans un nœud sibling)
                search_text = date_el.parent.get_text(' ') if date_el.parent else str(date_el)
                m = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})', search_text)
                if m:
                    mois_fr = {
                        'janvier': '01', 'février': '02', 'mars': '03', 'avril': '04',
                        'mai': '05', 'juin': '06', 'juillet': '07', 'août': '08',
                        'septembre': '09', 'octobre': '10', 'novembre': '11', 'décembre': '12',
                    }
                    mois = mois_fr.get(m.group(2).lower(), '01')
                    date_str = f"{m.group(3)}-{mois}-{int(m.group(1)):02d}"

            url_dossier, url_doc = '', ''
            for a in item.find_all('a', href=True):
                href = a['href']
                txt = a.get_text(strip=True).lower()
                if 'dossier' in txt or '/dossiers/' in href:
                    url_dossier = href if href.startswith('http') else AN_BASE + href
                elif 'document' in txt or '/projets/' in href or '/ta/' in href:
                    url_doc = href if href.startswith('http') else AN_BASE + href

            url = url_dossier or url_doc
            if not url:
                continue

            fiche_id = 'pjl-' + hashlib.md5(url.encode()).hexdigest()[:12]
            entries.append({
                'titre': titre,
                'description': '',
                'url': url,
                'url_dossier': url_dossier,
                'url_doc': url_doc,
                'date': date_str,
                'source': source['name'],
                'fiche_id': fiche_id,
            })

        # Fallback if the structure differs
        if not entries:
            for h3 in soup.find_all('h3'):
                titre = h3.get_text(strip=True)
                if not titre or len(titre) < 15:
                    continue

                parent = h3.find_parent(['li', 'div', 'article', 'section'])
                if not parent:
                    continue

                links = parent.find_all('a', href=True)
                url_dossier, url_doc = '', ''
                for a in links:
                    href = a['href']
                    txt = a.get_text(strip=True).lower()
                    if 'dossier' in txt or '/dossiers/' in href:
                        url_dossier = href if href.startswith('http') else AN_BASE + href
                    elif 'document' in txt or '/projets/' in href or '/ta/' in href:
                        url_doc = href if href.startswith('http') else AN_BASE + href

                url = url_dossier or url_doc
                if not url:
                    continue

                fiche_id = 'pjl-' + hashlib.md5(url.encode()).hexdigest()[:12]
                entries.append({
                    'titre': titre,
                    'description': '',
                    'url': url,
                    'url_dossier': url_dossier,
                    'url_doc': url_doc,
                    'date': today_str,
                    'source': source['name'],
                    'fiche_id': fiche_id,
                })

        log.info(f"Parlement scraper {source['name']} : {len(entries)} entrées")
    except Exception as e:
        log.warning(f"Parlement scraper {source['name']} : {e}")
    return entries


_MOIS_FR = {
    'janvier': '01', 'février': '02', 'mars': '03', 'avril': '04',
    'mai': '05', 'juin': '06', 'juillet': '07', 'août': '08',
    'septembre': '09', 'octobre': '10', 'novembre': '11', 'décembre': '12',
}


def _parse_fr_date(text: str) -> Optional[str]:
    """Parse a French date like '12 mars 2025' → '2025-03-12'."""
    m = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})', text.strip())
    if m:
        mois = _MOIS_FR.get(m.group(2).lower())
        if mois:
            return f"{m.group(3)}-{mois}-{int(m.group(1)):02d}"
    return None


def _scrape_deposit_date(url_dossier: str) -> Optional[str]:
    """
    Fetch the AN dossier page and extract the deposit date from the first
    Swiper slide (labelled 'Dépôt').  Returns 'YYYY-MM-DD' or None.
    """
    from bs4 import BeautifulSoup

    try:
        resp = requests.get(url_dossier, timeout=TIMEOUT, headers={'User-Agent': 'ABC-Veille/2.0'})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        slides = soup.find_all(class_='swiper-slide')
        for slide in slides:
            label_el = slide.find('span', class_=lambda c: c and '_bold' in c and '_colored-primary' in c)
            if not label_el:
                continue
            label = label_el.get_text(strip=True).lower()
            if 'dépôt' in label or 'depot' in label:
                date_el = slide.find('span', class_=lambda c: c and '_colored-grey' in c)
                if date_el:
                    return _parse_fr_date(date_el.get_text(strip=True))
        # If no "Dépôt" slide found, try first slide with a date
        for slide in slides:
            date_el = slide.find('span', class_=lambda c: c and '_colored-grey' in c)
            if date_el:
                d = _parse_fr_date(date_el.get_text(strip=True))
                if d:
                    return d
    except Exception as e:
        log.debug(f"scrape_deposit_date {url_dossier}: {e}")
    return None


def _crawl_pjl_content(url_dossier: str, url_doc: str = '') -> str:
    """
    Fetch meaningful content for a PJL to feed the LLM.
    Strategy:
    1. Parse the AN dossier page to find the Senate dossier link
       (Sénat has the exposé des motifs in plain HTML)
    2. If no Senate link (bill deposited at AN first), try url_doc directly
    3. Fallback: return content from the AN dossier page itself
    """
    from bs4 import BeautifulSoup

    senat_url = ''
    an_content = ''

    if url_dossier:
        try:
            resp = requests.get(url_dossier, timeout=TIMEOUT, headers={'User-Agent': 'ABC-Veille/2.0'})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')

            # Look for Senate dossier link
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'senat.fr' in href and ('dossier' in href.lower() or 'pjl' in href.lower() or 'leg' in href.lower()):
                    senat_url = href
                    break

            # Extract useful text from dossier page (title, stages, commission)
            an_content = soup.get_text(' ', strip=True)
            # Keep only the relevant middle part (skip nav boilerplate)
            an_content = an_content[:4000]
        except Exception as e:
            log.debug(f"crawl dossier {url_dossier}: {e}")

    # Try Senate page first (has exposé des motifs)
    if senat_url:
        try:
            resp_s = requests.get(senat_url, timeout=TIMEOUT, headers={'User-Agent': 'ABC-Veille/2.0'})
            resp_s.raise_for_status()
            # Fix encoding: senat.fr sometimes sends latin-1 with UTF-8 declaration
            enc = resp_s.encoding or 'utf-8'
            try:
                raw = resp_s.content.decode('utf-8')
            except UnicodeDecodeError:
                raw = resp_s.content.decode('latin-1')
            from bs4 import BeautifulSoup as _BS
            content = _BS(raw, 'html.parser').get_text(' ', strip=True)
            if content and len(content) > 500:
                log.debug(f"PJL content from Sénat ({len(content)} chars)")
                return content[:6000]
        except Exception as e:
            log.debug(f"crawl sénat {senat_url}: {e}")

    # Try AN document URL (bill deposited at AN first)
    if url_doc:
        try:
            resp_doc = requests.get(url_doc, timeout=TIMEOUT, headers={'User-Agent': 'ABC-Veille/2.0'})
            resp_doc.raise_for_status()
            from bs4 import BeautifulSoup as _BS2
            soup_doc = _BS2(resp_doc.text, 'html.parser')
            # Look for "Version HTML" link which has the actual bill text
            html_url = ''
            for a in soup_doc.find_all('a', href=True):
                txt_a = a.get_text(strip=True).lower()
                href = a['href']
                if 'html' in txt_a and ('texte' in href or 'contenu' in href or '.html' in href):
                    html_url = href if href.startswith('http') else 'https://www.assemblee-nationale.fr' + href
                    break
            if html_url:
                content = crawl_article(html_url)
                if content and len(content) > 500:
                    log.debug(f"PJL content from AN HTML version ({len(content)} chars)")
                    return content[:6000]
            # Fallback: use the doc page text itself
            content = soup_doc.get_text(' ', strip=True)
            if content and len(content) > 300:
                log.debug(f"PJL content from AN doc page ({len(content)} chars)")
                return content[:4000]
        except Exception as e:
            log.debug(f"crawl AN doc {url_doc}: {e}")

    # Fallback: AN dossier page text
    if an_content and len(an_content) > 200:
        log.debug(f"PJL content from AN dossier ({len(an_content)} chars)")
        return an_content

    return ''


def _scrape_dossier_stade(url_dossier: str) -> str:
    """
    Parse the AN dossier page using the Swiper slider structure.

    The page exposes a `.etape-slider` containing `swiper-slide` elements, one per
    legislative step. Each slide has:
      - span._bold._colored-primary  → étape label (e.g. "Première lecture au Sénat")
      - span._colored-grey._small    → date (if the step has started)
      - span._small._bold            → completion status (e.g. "Texte adopté ✅")

    The last dated slide is the current stage. Its completion badge (for example
    "Texte adopté") describes that stage, not the dossier's current stage.
    """
    from bs4 import BeautifulSoup

    try:
        resp = requests.get(url_dossier, timeout=TIMEOUT, headers={'User-Agent': 'ABC-Veille/2.0'})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        # ── Try the structured Swiper slider first ────────────────────
        slides = soup.find_all(class_='swiper-slide')
        if slides:
            current_stade = ''
            current_date = ''
            for slide in slides:
                label_el = slide.find('span', class_=lambda c: c and '_bold' in c and '_colored-primary' in c)
                date_el  = slide.find('span', class_=lambda c: c and '_colored-grey' in c)
                if not label_el:
                    continue
                label = label_el.get_text(' ', strip=True)
                date_txt = date_el.get_text(strip=True) if date_el else ''
                date_match = re.search(
                    r'\b(?:\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2})\b',
                    date_txt,
                    re.IGNORECASE,
                )
                if date_match and label:
                    completion_el = slide.find(
                        'span',
                        class_=lambda c: c and '_small' in c and '_bold' in c,
                    )
                    completion_text = (
                        completion_el.get_text(' ', strip=True)
                        if completion_el else ''
                    )
                    current_stade = (
                        'Texte rejeté'
                        if re.search(r'\btexte rejet[ée]\b', completion_text, re.IGNORECASE)
                        else _detect_stade_rss(label)
                    )
                    current_date = date_match.group(0)
            if current_stade:
                log.debug(
                    f"stade (swiper, {current_date}) : {current_stade} — {url_dossier}"
                )
                return current_stade
            if current_date:
                log.warning(
                    f"Parlement : étape AN non reconnue ({current_date}) — {url_dossier}"
                )
                return ''

        log.warning(f"Parlement : aucune étape datée reconnue sur la page AN — {url_dossier}")
        return ''

    except Exception as e:
        log.warning(f"scrape_dossier_stade {url_dossier}: {e}")
        return ''


def _load_fiches(path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception as e:
            log.warning(f"Parlement : impossible de lire les fiches ({e}), reset")
    return {}


def _save_fiches(path, fiches: dict):
    path.write_text(json.dumps(fiches, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info(f"parlement_fiches.json : {len(fiches)} fiches")


def fetch_parlement(script_dir, today_str: str) -> Tuple[list, list, str]:
    """
    Scrape AN pages (PJL + adopted texts), filter relevant government PJLs,
    update tracking and return:
      (fiches_list, pjl_autres, pjl_briefing)
    """
    PARLEMENT_FICHES = script_dir / 'parlement_fiches.json'
    PJL_CACHE = script_dir / 'data' / 'pjl_cache.json'  # Complete analysis cache, not just rejections

    log.info("=== Parlement ===")
    fiches = _load_fiches(PARLEMENT_FICHES)
    maj = 0
    groq_used = 0
    non_env_rejected = 0
    quota_skipped = 0
    cache_hits = 0

    # Load PJL analysis cache (all analyses with full data, expire after 90 days)
    try:
        pjl_cache: dict = json.loads(PJL_CACHE.read_text(encoding='utf-8')) if PJL_CACHE.exists() else {}
    except Exception:
        pjl_cache = {}
    cutoff_cache = (datetime.now() - timedelta(days=90)).isoformat()
    pjl_cache = {u: d for u, d in pjl_cache.items() if d.get('analyse_date', '') >= cutoff_cache}
    cache_skipped = 0

    # ── Sync Supabase — dossiers manuels uniquement ───────────────────
    # La création automatique de fiches a été supprimée (commit 8db4418).
    # Seuls les dossiers ajoutés manuellement depuis l'UI sont trackés.
    sync = SupabaseSync()
    if sync.ready:
        # Charger les dossiers ajoutés manuellement depuis l'UI
        manuel_rows = sync.load_manuel_dossiers()
        for row in manuel_rows:
            url_an = row.get('url_an', '')
            if not url_an:
                continue
            fiche_id = 'pjl-' + hashlib.md5(url_an.encode()).hexdigest()[:12]
            if fiche_id not in fiches:
                # Importer le dossier Supabase dans le cache local
                fiches[fiche_id] = {
                    'id':          fiche_id,
                    'titre':       row.get('titre', ''),
                    'date_depot':  str(row.get('date_depot') or today_str),
                    'stade':       row.get('stade', 'Dépôt'),
                    'stade_index': row.get('stade_index', 0),
                    'url_an':      url_an,
                    'url_dossier': row.get('url_dossier', ''),
                    'source_rss':  'manuel',
                    'resume_abc':  row.get('resume_abc', ''),
                    'pourquoi':    row.get('pourquoi', ''),
                    'score':       row.get('score', 2),
                    'horizon':     row.get('horizon', ''),
                    'nouveau_stade': False,
                    'manuel':      True,
                    'historique':  [{'date': today_str, 'stade': row.get('stade', 'Dépôt'), 'event': 'Importé depuis UI'}],
                    'created_at':  today_str,
                    'updated_at':  today_str,
                    'supabase_id': row.get('id'),
                    'statut':      row.get('statut', 'a_surveiller'),
                }
                log.info(f"Parlement: dossier manuel importé depuis Supabase — {row.get('titre','')[:50]}")

    all_entries = []
    for source in AN_SCRAPER_SOURCES:
        all_entries.extend(_scrape_an_listing(source, today_str))

    # Deduplicate by fiche_id
    seen_ids = set()
    entries = []
    for e in all_entries:
        if e['fiche_id'] not in seen_ids:
            seen_ids.add(e['fiche_id'])
            entries.append(e)

    # Filter: keep only entries from the last 31 days (or with no date found)
    cutoff_date = (datetime.now() - timedelta(days=31)).strftime('%Y-%m-%d')
    before = len(entries)
    entries = [e for e in entries if e['date'] == today_str or e['date'] >= cutoff_date]
    if before - len(entries):
        log.info(f"Parlement : {before - len(entries)} entrées exclues (plus anciennes que 31 jours)")

    log.info(f"Parlement : {len(entries)} entrées uniques après déduplication")
    if len(entries) == 0:
        log.warning("Parlement : aucune entrée scrappée — vérifier l'AN")

    # Briefing LLM
    try:
        pjl_briefing = groq_briefing_parlement(entries, today_str)
    except Exception as e:
        log.warning(f"Parlement briefing erreur : {e}")
        pjl_briefing = ''

    # Process each scraped entry
    for entry in entries:
        titre = entry['titre']
        description = entry.get('description', '')
        fiche_id = entry['fiche_id']
        stade = _detect_stade_rss(titre, description)

        if fiche_id in fiches:
            fiche = fiches[fiche_id]
            fiche['nouveau_stade'] = False
            url_dossier = fiche.get('url_dossier') or entry.get('url_dossier', '')
            if url_dossier:
                stade_actuel = _scrape_dossier_stade(url_dossier) or stade
                if stade_actuel and stade_actuel != fiche['stade']:
                    ancien_stade = fiche['stade']
                    log.info(f"Parlement avancement : {titre[:50]} [{ancien_stade} → {stade_actuel}]")
                    fiche.setdefault('historique', []).append({
                        'date': today_str,
                        'stade': stade_actuel,
                        'event': f"Avancement : {ancien_stade} → {stade_actuel}",
                    })
                    fiche['stade'] = stade_actuel
                    fiche['stade_index'] = STADES_ORDRE.index(stade_actuel) if stade_actuel in STADES_ORDRE else -1
                    fiche['nouveau_stade'] = True
                    fiche['updated_at'] = today_str
                    maj += 1
                    # Sync Supabase
                    if sync.ready:
                        sync.upsert_dossier(fiche)
                        sync.record_stage_change(fiche, ancien_stade, stade_actuel)

            # Re-analyse if resume_abc is missing (e.g. after a data reset)
            if not fiche.get('resume_abc') and groq_used < PARLEMENT_MAX_GROQ:
                if not _is_pjl_non_environmental(titre):
                    url_doc = entry.get('url_doc', '')
                    content = _crawl_pjl_content(url_dossier, url_doc) if (url_dossier or url_doc) else ''
                    analysis = groq_analyse_pjl(titre, content or titre)
                    groq_used += 1
                    if analysis.get('pertinent'):
                        fiche['resume_abc'] = analysis.get('resume', '')
                        fiche['pourquoi'] = analysis.get('pourquoi', '')
                        fiche['score'] = int(analysis.get('score') or 1)
                        fiche['horizon'] = analysis.get('horizon', '')
                        if sync.ready:
                            sync.upsert_dossier(fiche)
                        log.info(f"Parlement re-analyse : {titre[:50]}")
            continue

        # Filter 1: keyword_match
        if not keyword_match(titre + ' ' + description):
            continue

        # Filter 2: government PJL
        if not _is_pjl_gouvernemental(titre):
            continue

        # Filter 3: check cache
        url_entry = entry.get('url', '')
        if url_entry in pjl_cache:
            # Use cached analysis instead of calling LLM again
            analysis = pjl_cache[url_entry].copy()
            analysis.pop('analyse_date', None)  # Remove cache date from analysis
            cache_hits += 1
            cache_skipped += 1
        else:
            # Filter 4: Pre-filter non-environmental PJLs
            if _is_pjl_non_environmental(titre):
                non_env_rejected += 1
                continue

            # Filter 5: Groq quota for new PJLs
            if groq_used >= PARLEMENT_MAX_GROQ:
                quota_skipped += 1
                continue

            # Crawl real content before LLM analysis
            url_dossier = entry.get('url_dossier', '')
            url_doc = entry.get('url_doc', '')
            content = _crawl_pjl_content(url_dossier, url_doc) if (url_dossier or url_doc) else ''
            description = content or titre

            analysis = groq_analyse_pjl(titre, description)
            groq_used += 1
            
            # Save to cache (both pertinent and non-pertinent)
            pjl_cache[url_entry] = {
                'titre': titre,
                'analyse_date': today_str,
                'pertinent': analysis.get('pertinent', False),
                'score': int(analysis.get('score') or 1),
                'resume': analysis.get('resume', ''),
                'pourquoi': analysis.get('pourquoi', ''),
                'horizon': analysis.get('horizon', ''),
            }

        # ✅ Analysis is cached, but NO automatic fiche creation
        # Only manually-added dossiers by user are tracked in Supabase

    # Step 3: manually added dossiers update stage (manuel=True)
    for fiche_id, fiche in fiches.items():
        if not fiche.get('manuel'):
            continue
        fiche_id in seen_ids or seen_ids.add(fiche_id)
        fiche['nouveau_stade'] = False
        url_dossier = fiche.get('url_dossier', '')
        if not url_dossier:
            continue
        try:
            stade_actuel = _scrape_dossier_stade(url_dossier)
            if stade_actuel and stade_actuel != fiche.get('stade', ''):
                ancien_stade = fiche.get('stade', '?')
                log.info(
                    f"Parlement manuel avancement : {fiche['titre'][:50]} "
                    f"[{ancien_stade} → {stade_actuel}]"
                )
                fiche.setdefault('historique', []).append({
                    'date': today_str,
                    'stade': stade_actuel,
                    'event': f"Avancement : {ancien_stade} → {stade_actuel}",
                })
                fiche['stade'] = stade_actuel
                fiche['stade_index'] = STADES_ORDRE.index(stade_actuel) if stade_actuel in STADES_ORDRE else -1
                fiche['nouveau_stade'] = True
                fiche['updated_at'] = today_str
                maj += 1
                # Sync Supabase
                if sync.ready:
                    sync.upsert_dossier(fiche)
                    sync.record_stage_change(fiche, ancien_stade, stade_actuel)
        except Exception as e:
            log.debug(f"Parlement manuel MAJ {fiche_id}: {e}")

    # Reset nouveau_stade for dossiers not seen (unless manual)
    for fiche_id, fiche in fiches.items():
        if fiche_id not in seen_ids and not fiche.get('manuel'):
            fiche['nouveau_stade'] = False

    _save_fiches(PARLEMENT_FICHES, fiches)

    # Persist complete analysis cache
    try:
        PJL_CACHE.parent.mkdir(exist_ok=True)
        PJL_CACHE.write_text(json.dumps(pjl_cache, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        log.warning(f"Parlement : impossible de sauvegarder pjl_cache.json : {e}")

    log.info(
        f"Parlement : {maj} avancements, "
        f"{groq_used} appels Groq, {non_env_rejected} rejetés (hors-env), "
        f"{quota_skipped} différés (quota), {cache_hits} hits cache"
    )

    # Re-analyse all fiches loaded from Supabase that have no resume_abc
    # (handles cases where the entry is no longer in today's RSS feed)
    # Uses its own quota (5 max) independent of the new-PJL quota
    orphan_groq = 0
    for fiche_id, fiche in fiches.items():
        if fiche.get('resume_abc') or fiche.get('manuel'):
            continue
        if orphan_groq >= 5:
            break
        titre = fiche.get('titre', '')
        # Pre-filter non-environmental PJLs
        if _is_pjl_non_environmental(titre):
            continue
        url_dossier = fiche.get('url_dossier', '')
        content = _crawl_pjl_content(url_dossier) if url_dossier else ''
        analysis = groq_analyse_pjl(titre, content or titre)
        orphan_groq += 1
        if analysis.get('pertinent'):
            fiche['resume_abc'] = analysis.get('resume', '')
            fiche['pourquoi'] = analysis.get('pourquoi', '')
            fiche['score'] = int(analysis.get('score') or 1)
            fiche['horizon'] = analysis.get('horizon', '')
            if sync.ready:
                sync.upsert_dossier(fiche)
            log.info(f"Parlement re-analyse (orphan) : {titre[:50]}")

    if orphan_groq > 0:
        _save_fiches(PARLEMENT_FICHES, fiches)

    # pjl_autres grouped by source
    groups: Dict[str, list] = {}
    for e in entries:
        date = e['date']
        if date == today_str and e.get('url_dossier'):
            real_date = _scrape_deposit_date(e['url_dossier'])
            if real_date:
                date = real_date
                log.debug(f"Parlement date dépôt récupérée : {date} — {e['titre'][:50]}")

        src = e['source']
        if src not in groups:
            groups[src] = []
        groups[src].append({
            'titre': e['titre'],
            'url': e['url'],
            'url_dossier': e.get('url_dossier', ''),
            'date': date,
        })
    pjl_autres = [{'source': src, 'items': items} for src, items in groups.items()]

    fiches_list = sorted(
        fiches.values(),
        key=lambda f: (f.get('score', 1), f.get('stade_index', 0)),
        reverse=True,
    )
    return fiches_list, pjl_autres, pjl_briefing
