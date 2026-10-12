import io
import logging
import re
import tarfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests

from ..config import JORF_BASE_URL, TIMEOUT
from ..crawl import crawl_article
from ..filters import categorise, keyword_match, make_id
from ..llm import groq_analyse_jorf, groq_briefing_jorf, groq_analyse_batch

log = logging.getLogger(__name__)


def list_jorf_files() -> list:
    try:
        resp = requests.get(JORF_BASE_URL, timeout=TIMEOUT)
        resp.raise_for_status()
        return sorted(re.findall(r'href="(JORF[^"]*\.tar\.gz)"', resp.text))
    except Exception as e:
        log.warning(f"JORF listing error : {e}")
        return []


def get_today_jorf_url(today_compact: str):
    files = list_jorf_files()
    today_files = [f for f in files if today_compact in f]
    chosen = today_files[-1] if today_files else (files[-1] if files else None)
    if chosen:
        log.info(f"Fichier JORF sélectionné : {chosen}")
        return urljoin(JORF_BASE_URL, chosen)
    return None


def parse_jorf_xml(content: bytes, today_str: str):
    import xml.etree.ElementTree as ET

    articles = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        log.debug(f"XML parse error : {e}")
        return articles

    seen_nor = set()

    for texte in root.iter('TEXTE'):
        nor = (texte.get('nor', '') or '').strip()
        titre_el = texte.find('TITRE_TXT')
        titre = (titre_el.text or '').strip() if titre_el is not None else ''

        if not titre:
            continue
        if nor and nor in seen_nor:
            continue
        if nor:
            seen_nor.add(nor)

        nature = texte.get('nature', '')
        ministere = texte.get('ministere', '')
        date_pub = texte.get('date_publi', today_str)
        cid = texte.get('cid', '')

        url = f'https://www.legifrance.gouv.fr/jorf/id/{cid}' if cid else (
            f'https://www.legifrance.gouv.fr/jorf/id/{nor}' if nor else ''
        )
        contenu = ' — '.join(filter(None, [nature, ministere]))

        articles.append({
            'titre': titre,
            'contenu': contenu[:500],
            'url': url,
            'nor': nor,
            'date': date_pub,
            'keyword_match': keyword_match(titre + ' ' + contenu),
        })

    return articles


def fetch_jorf(today_str: str):
    """Fetch and analyze daily JORF texts for ABC relevance."""
    log.info("=== JORF ===")
    items, autres, total_analysed, briefing = [], [], 0, []

    today_compact = today_str.replace('-', '')
    try:
        url = get_today_jorf_url(today_compact)
        if not url:
            log.warning("Aucun fichier JORF disponible")
            return items, autres, 0, briefing

        log.info(f"Téléchargement : {url}")
        resp = requests.get(url, timeout=180)
        resp.raise_for_status()

        with tarfile.open(fileobj=io.BytesIO(resp.content), mode='r:gz') as tar:
            xml_members = [m for m in tar.getmembers() if m.name.endswith('.xml')]
            log.info(f"JORF : {len(xml_members)} fichiers XML")

            all_raw = []
            seen_nor_global = set()
            # Only keep texts published within the last 7 days
            from datetime import timedelta
            date_cutoff = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            for member in xml_members:
                try:
                    f = tar.extractfile(member)
                    if f:
                        for art in parse_jorf_xml(f.read(), today_str):
                            nor = art.get('nor', '')
                            if nor and nor in seen_nor_global:
                                continue
                            if nor:
                                seen_nor_global.add(nor)
                            # Skip texts with old date_publi (consolidations of older laws)
                            if art.get('date', today_str) < date_cutoff:
                                log.debug(f"JORF texte ancien ignoré ({art.get('date')}) : {art['titre'][:50]}")
                                continue
                            all_raw.append(art)
                except Exception as e:
                    log.debug(f"Erreur XML {member.name} : {e}")

            if len(all_raw) == 0:
                log.warning("JORF : aucun texte extrait du tar.gz — vérifier l'URL")

            # Séparer textes à analyser (keyword match) des autres
            all_articles = [a for a in all_raw if a.get('keyword_match')]
            non_keyword = [a for a in all_raw if not a.get('keyword_match')]

            # Tous les non-keyword vont directement dans autres
            for art in non_keyword:
                autres.append({
                    'titre': art['titre'],
                    'nature': art.get('contenu', '').split(' — ')[0],
                    'url': art.get('url', ''),
                    'date': art.get('date', today_str),
                })

            total_analysed = len(all_articles)
            log.info(f"JORF : {len(all_raw)} textes uniques, {total_analysed} analysés par LLM, {len(non_keyword)} → autres directement")

            try:
                briefing = groq_briefing_jorf(all_articles, today_str)
                log.info(f"JORF briefing : {'OK' if briefing else 'vide'} ({len(briefing)} cars)")
            except Exception as e:
                log.warning(f"JORF briefing erreur (non bloquant) : {e}")
                briefing = ''

            # Utilise le batching pour optimiser le quota Groq
            articles_with_content = []
            for art in all_articles:
                contenu = art['contenu']
                if art.get('url') and 'legifrance' in art.get('url', ''):
                    full = crawl_article(art['url'])
                    if full:
                        contenu = full
                articles_with_content.append({'titre': art['titre'], 'contenu': contenu, 'art': art})

            # Traite par lots de 2 pour rester dans les limites
            batch_size = 2
            analyses = []
            for i in range(0, len(articles_with_content), batch_size):
                batch = articles_with_content[i:i + batch_size]
                batch_analyses = groq_analyse_batch(
                    [{'titre': a['titre'], 'contenu': a['contenu']} for a in batch],
                    prompt_type="jorf"
                )
                for a, analysis in zip(batch, batch_analyses):
                    analyses.append((a['art'], analysis))

            for art, analysis in analyses:
                try:
                    resume = analysis.get('resume', '')
                    # Reject if LLM resume signals non-relevance despite pertinent=True
                    _neg = resume.lower()
                    if any(w in _neg for w in ['aucun lien', 'aucune obligation', 'non pertinent', 'pas de lien', 'sans lien']):
                        analysis['pertinent'] = False

                    if analysis.get('pertinent') is False:
                        autres.append({
                            'titre': art['titre'],
                            'nature': art.get('contenu', '').split(' — ')[0],
                            'url': art.get('url', ''),
                            'date': art.get('date', today_str),
                        })
                        log.debug(f"JORF → autres : {art['titre'][:60]}")
                        continue

                    score = int(analysis.get('score', 2))
                    pourquoi = analysis.get('pourquoi', '')
                    if score < 2:
                        pourquoi = ''

                    items.append({
                        'id': make_id('JORF', art['titre']),
                        'source': 'JORF',
                        'categorie': categorise(art['titre'] + ' ' + art.get('contenu', '')),
                        'titre': art['titre'],
                        'resume': analysis.get('resume') or art['titre'],
                        'pourquoi': pourquoi,
                        'criticite': score,
                        'url': art.get('url', ''),
                        'date': today_str,
                    })
                    log.debug(f"JORF retenu (score={score}) : {art['titre'][:60]}")
                except Exception as e:
                    log.debug(f"Erreur analyse JORF : {e}")

    except Exception as e:
        log.error(f"JORF fatal : {e}", exc_info=True)

    log.info(f"JORF : {len(items)} retenus / {total_analysed} analysés ({len(autres)} autres)")
    return items, autres, total_analysed, briefing

