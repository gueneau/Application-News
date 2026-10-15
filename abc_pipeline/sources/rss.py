import html
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import requests

from ..config import RSS_SOURCES, TIMEOUT
from ..crawl import crawl_article, crawl_article_links, crawl_article_links_filtered, crawl_playwright_links, get_crawled_date
from ..filters import make_id, reglements_match, valid_category
from ..llm import groq_analyse_batch, groq_analyse_rss

log = logging.getLogger(__name__)

_MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'janvier': 1, 'février': 2, 'mars': 3, 'avril': 4,
    'mai': 5, 'juin': 6, 'juillet': 7, 'août': 8,
    'septembre': 9, 'octobre': 10, 'novembre': 11, 'décembre': 12,
}

def _date_from_text(text: str):
    """Extract a date embedded in text.

    Handles:
    - '2 April 2020' / '5 décembre 2025'  (littéral)
    - '17/03/2026' or '17/03/2026 16:19'  (numérique FR, ex: AEF)
    """
    # Littéral : "2 April 2020"
    m = re.search(r'(\d{1,2})\s+([A-Za-zéûîôàè]+)\s+(\d{4})', text)
    if m:
        month = _MONTHS.get(m.group(2).lower())
        if month:
            try:
                return datetime(int(m.group(3)), month, int(m.group(1)))
            except ValueError:
                pass
    # Numérique FR : "17/03/2026" ou "17/03/2026 16:19"
    m2 = re.search(r'\b(\d{1,2})/(\d{2})/(\d{4})\b', text)
    if m2:
        try:
            return datetime(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))
        except ValueError:
            pass
    return None


def _rss_cutoff(now: datetime) -> datetime:
    if now.weekday() == 0:
        return (now - timedelta(days=3)).replace(
            hour=18, minute=0, second=0, microsecond=0
        )
    return now - timedelta(hours=24)


def _review_rss_candidates(
    source_name: str,
    candidates: list,
    today_str: str,
    new_seen: dict = None,
    freshness_cutoff: datetime = None,
    existing_by_id: dict = None,
) -> list:
    """Batch-screen recent candidates, then validate promising items on article text.

    Candidates already decided earlier today (same source + title, found in
    existing_by_id) are reused verbatim — no LLM call — so an article
    retained this morning doesn't silently disappear or flip on a re-run.
    """
    if not candidates:
        return []

    items = []
    if existing_by_id:
        still_new = []
        for candidate in candidates:
            cached = existing_by_id.get(make_id(source_name, candidate['titre']))
            if cached is not None:
                items.append(cached)
            else:
                still_new.append(candidate)
        candidates = still_new
        if not candidates:
            return items

    priority_candidates = [
        candidate for candidate in candidates
        if reglements_match(candidate['titre'])
    ]
    screening_candidates = [
        candidate for candidate in candidates
        if not reglements_match(candidate['titre'])
    ]
    promising = list(priority_candidates)
    if screening_candidates:
        try:
            screening = groq_analyse_batch(
                [
                    {
                        'titre': candidate['titre'],
                        'contenu': candidate.get('contenu', ''),
                        'themes': candidate.get('themes', []),
                    }
                    for candidate in screening_candidates
                ],
                prompt_type='rss',
                stage='screen',
            )
            if len(screening) != len(screening_candidates):
                raise ValueError("réponse batch incomplète")
            promising.extend(
                candidate for candidate, result in zip(screening_candidates, screening)
                if result.get('pertinent') is True
            )
        except Exception as e:
            # Keep recall if the batch response is unusable; the full-text review
            # remains the publication gate.
            log.warning(
                f"RSS {source_name} : présélection batch indisponible ({e}); "
                "vérification individuelle des candidats"
            )
            promising.extend(screening_candidates)

    for candidate in promising:
        titre = candidate['titre']
        url = candidate.get('url', '')
        # Le résumé du flux RSS est déjà du texte propre (titre/description de
        # l'éditeur) ; on le place en tête et on ajoute le crawl de la page en
        # complément. Un simple "crawl or résumé" jetait le résumé dès que le
        # crawl renvoyait quoi que ce soit de non-vide — y compris un bandeau
        # cookies/paywall sans aucun rapport avec l'article (cf. actu-environnement.com).
        feed_contenu = candidate.get('contenu', '')
        crawled = crawl_article(url) if url else ''
        contenu = f"{feed_contenu} {crawled}".strip()
        article_date = candidate.get('date') or today_str

        if freshness_cutoff and url:
            html_date = get_crawled_date(url)
            if html_date:
                try:
                    html_dt = datetime.strptime(html_date, '%Y-%m-%d')
                    if html_dt < freshness_cutoff:
                        log.debug(
                            f"RSS date HTML trop ancienne ({html_date}), exclu : {titre[:60]}"
                        )
                        continue
                    article_date = html_date
                except ValueError:
                    log.warning(f"RSS date HTML invalide ({html_date}) : {url}")

        try:
            analysis = groq_analyse_rss(titre, contenu)
        except Exception as e:
            log.warning(f"RSS validation finale {source_name} ({titre[:60]}) : {e}")
            continue
        if analysis.get('pertinent') is not True:
            log.debug(f"Non pertinent après lecture complète, exclu : {titre[:60]}")
            continue

        score = int(analysis.get('score') or 1)
        items.append({
            'id': make_id(source_name, titre),
            'source': source_name,
            'categorie': valid_category(analysis.get('categorie')),
            'titre': titre,
            'resume': analysis.get('resume') or titre,
            'pourquoi': analysis.get('pourquoi', '') if score >= 2 else '',
            'criticite': score,
            'url': url,
            'date': article_date,
        })
        if new_seen is not None and url:
            new_seen[url] = today_str

    log.info(
        f"RSS {source_name} : {len(candidates)} nouveau(x) candidat(s), {len(promising)} à vérifier, "
        f"{len(items)} retenus au total"
    )
    return items


def fetch_rss_source(source: dict, today_str: str, seen_urls: dict = None, new_seen: dict = None, existing_by_id: dict = None):
    items = []
    name = source['name']

    # ── Route Playwright : sources JS-rendered (ex: Google News publication) ──
    if source.get('playwright_crawl'):
        playwright_url = source['playwright_crawl']
        log.info(f"Playwright → {playwright_url}")
        try:
            article_links = crawl_playwright_links(playwright_url, max_links=15)
            log.info(f"Playwright {name} : {len(article_links)} liens extraits")
            fallback_cutoff = _rss_cutoff(datetime.now())
            candidates = []
            for link in article_links:
                try:
                    if seen_urls is not None and link['url'] in seen_urls:
                        continue
                    titre_art = link.get('titre', '')
                    title_dt = _date_from_text(titre_art)
                    titre_art = re.sub(
                        r'\s*\d{1,2}/\d{2}/\d{4}.*$', '', titre_art
                    ).strip()
                    if not titre_art or len(titre_art) < 10:
                        continue
                    if title_dt and title_dt < fallback_cutoff:
                        log.debug(f"Playwright date titre trop ancienne ({title_dt.date()}), exclu : {titre_art[:60]}")
                        continue
                    candidates.append({
                        'titre': titre_art,
                        'url': link['url'],
                        'contenu': '',
                        'date': today_str,
                        'themes': source.get('require_keywords', []),
                    })
                except Exception as e_art:
                    log.debug(f"Playwright article {link['url']} : {e_art}")
            items.extend(_review_rss_candidates(
                name,
                candidates,
                today_str,
                new_seen=new_seen,
                freshness_cutoff=fallback_cutoff,
                existing_by_id=existing_by_id,
            ))
        except Exception as e:
            log.warning(f"Playwright {name} error : {e}")
        return items

    try:
        try:
            rss_resp = requests.get(
                source['url'],
                timeout=TIMEOUT,
                headers={
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                },
            )
            content = rss_resp.content
            # Repair malformed XML (e.g. undefined HTML entities in Carbone 4 feed)
            # using lxml's recovery parser before handing off to feedparser.
            try:
                from lxml import etree
                parser = etree.XMLParser(recover=True, resolve_entities=False)
                root = etree.fromstring(content, parser)
                content = etree.tostring(root, xml_declaration=True, encoding='utf-8')
            except Exception:
                pass
            feed = feedparser.parse(content)
        except Exception:
            feed = feedparser.parse(source['url'])

        if feed.bozo and not feed.entries:
            raise ValueError(f"Feed invalide : {feed.bozo_exception}")

        entries = feed.entries[:15]
        log.info(f"RSS {name} : {len(entries)} entrées")

        cutoff_dt = _rss_cutoff(datetime.now())
        candidates = []
        for entry in entries:
            titre = (entry.get('title') or '').strip()
            if not titre:
                continue

            contenu = html.unescape(re.sub(
                r'<[^>]+>',
                ' ',
                (
                    entry.get('summary')
                    or entry.get('description')
                    or (entry.get('content') or [{}])[0].get('value')
                    or ''
                ),
            )).strip()

            url = entry.get('link', '')

            pub = entry.get('published_parsed') or entry.get('updated_parsed')
            article_dt = None
            article_date = today_str
            if pub:
                try:
                    article_dt = datetime.fromtimestamp(time.mktime(pub))
                    article_date = article_dt.strftime('%Y-%m-%d')
                except Exception:
                    pass
            if article_dt is None:
                # feedparser couldn't auto-parse — try the raw string
                raw = entry.get('published') or entry.get('updated') or ''
                if raw:
                    try:
                        article_dt = parsedate_to_datetime(raw).astimezone(timezone.utc).replace(tzinfo=None)
                        article_date = article_dt.strftime('%Y-%m-%d')
                    except Exception:
                        try:
                            article_dt = datetime.fromisoformat(raw.replace('Z', '+00:00')).replace(tzinfo=None)
                            article_date = article_dt.strftime('%Y-%m-%d')
                        except Exception:
                            pass

            if article_dt is None or article_dt < cutoff_dt:
                log.debug(f"Article sans date ou trop ancien ({article_date}), exclu : {titre[:60]}")
                continue

            # Certains flux (ex: Carbone 4) publient pubDate=aujourd'hui sur de vieux articles.
            # Si une date est lisible dans le titre et qu'elle est ancienne → exclure.
            title_dt = _date_from_text(titre)
            if title_dt and title_dt < cutoff_dt:
                log.debug(f"Date dans le titre trop ancienne ({title_dt.date()}), exclu : {titre[:60]}")
                continue

            candidates.append({
                'titre': titre,
                'url': url,
                'contenu': contenu[:500],
                'date': article_date,
                'themes': source.get('require_keywords', []),
            })
        items.extend(_review_rss_candidates(name, candidates, today_str, existing_by_id=existing_by_id))

    except Exception as e:
        log.warning(f"RSS {name} error : {e}")

        if source.get('fallback_crawl'):
            fallback_url = source['fallback_crawl'].replace('{today}', today_str)
            base_url = '/'.join(fallback_url.split('/')[:3])
            try:
                log.info(f"Fallback → {fallback_url}")
                url_pat = source.get('article_url_contains', '')
                if url_pat:
                    article_links = crawl_article_links_filtered(fallback_url, base_url, url_pat, max_links=10)
                else:
                    article_links = crawl_article_links(fallback_url, base_url, max_links=5)

                if not article_links:
                    contenu = crawl_article(fallback_url)
                    if contenu:
                        items.extend(_review_rss_candidates(
                            name,
                            [{
                                'titre': f'Actualités {name}',
                                'url': fallback_url,
                                'contenu': contenu[:500],
                                'date': today_str,
                                'themes': source.get('require_keywords', []),
                            }],
                            today_str,
                            existing_by_id=existing_by_id,
                        ))
                else:
                    fallback_cutoff = _rss_cutoff(datetime.now())
                    candidates = []
                    for link in article_links:
                        try:
                            if seen_urls is not None and link['url'] in seen_urls:
                                log.debug(f"Fallback déjà vu, ignoré : {link['url']}")
                                continue
                            titre_art = link['titre']
                            # Extract date from raw title BEFORE cleaning (AEF embeds date at end)
                            title_dt = _date_from_text(titre_art)
                            # Clean navigation prefixes/suffixes (e.g. AEF: "À LA UNEVrai titre12/03/2026...")
                            titre_art = re.sub(r'^(À LA UNE|INTERVIEW|ANALYSE|TRIBUNE|REPORTAGE|EXCLUSIF)\s*', '', titre_art, flags=re.IGNORECASE).strip()
                            titre_art = re.sub(r'\s*\d{1,2}/\d{2}/\d{4}.*$', '', titre_art).strip()
                            titre_art = re.sub(r'\s*Publiée?\s+le\s+.*$', '', titre_art, flags=re.IGNORECASE).strip()
                            titre_art = re.sub(r'\s*-\s*Dépêche\s+n°\s*\d+.*$', '', titre_art, flags=re.IGNORECASE).strip()
                            if not titre_art or len(titre_art) < 15:
                                continue
                            # If no date in raw title, try cleaned title (e.g. Carbone 4 "Guidelines2 April 2020")
                            if title_dt is None:
                                title_dt = _date_from_text(titre_art)
                            if title_dt and title_dt < fallback_cutoff:
                                log.debug(f"Fallback date titre trop ancienne ({title_dt.date()}), exclu : {titre_art[:60]}")
                                continue
                            # Sources like AEF always embed the date — if absent, article is likely stale/nav
                            if title_dt is None and source.get('require_date_in_title'):
                                log.debug(f"Fallback sans date détectable, exclu : {titre_art[:60]}")
                                continue

                            candidates.append({
                                'titre': titre_art,
                                'url': link['url'],
                                'contenu': '',
                                'date': today_str,
                                'themes': source.get('require_keywords', []),
                            })
                        except Exception as e_art:
                            log.debug(f"Fallback article {link['url']} : {e_art}")
                    items.extend(_review_rss_candidates(
                        name,
                        candidates,
                        today_str,
                        new_seen=new_seen,
                        freshness_cutoff=fallback_cutoff,
                        existing_by_id=existing_by_id,
                    ))

            except Exception as e2:
                log.warning(f"Fallback {name} error : {e2}")

    return items


def fetch_rss(today_str: str, script_dir: Path = None, existing_items: list = None):
    log.info("=== RSS ===")

    # ── Cache des URLs déjà vus pour les sources fallback_crawl ──────────
    seen_cache: dict = {}
    seen_path: Path | None = None
    if script_dir:
        seen_path = script_dir / 'data' / 'rss_seen.json'
        if seen_path.exists():
            try:
                seen_cache = json.loads(seen_path.read_text(encoding='utf-8'))
            except Exception:
                seen_cache = {}
        # Purge entries older than 30 days
        cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        seen_cache = {url: d for url, d in seen_cache.items() if d >= cutoff}

    new_seen: dict = {}
    existing_by_id = {item['id']: item for item in (existing_items or []) if item.get('id')}

    all_items = []
    for source in RSS_SOURCES:
        is_fallback = bool(source.get('fallback_crawl') or source.get('playwright_crawl'))
        items = fetch_rss_source(
            source, today_str,
            seen_urls=seen_cache if is_fallback else None,
            new_seen=new_seen if is_fallback else None,
            existing_by_id=existing_by_id,
        )
        if not items:
            log.warning(f"SOURCE VIDE : {source['name']} — 0 articles retenus")
        all_items.extend(items)
        log.info(f"RSS {source['name']} : {len(items)} retenus")

    # Carry forward items decided earlier today whose source article has
    # since scrolled out of its feed's recent-entries window (so they were
    # never even re-presented as a candidate above) — they must not vanish.
    seen_ids = {item['id'] for item in all_items if item.get('id')}
    carried = [item for item_id, item in existing_by_id.items() if item_id not in seen_ids]
    if carried:
        log.info(f"RSS : {len(carried)} item(s) repris tel quel (sortis de la fenêtre des flux)")
        all_items.extend(carried)

    # Save updated seen cache
    if seen_path is not None and new_seen:
        seen_cache.update(new_seen)
        seen_path.parent.mkdir(parents=True, exist_ok=True)
        seen_path.write_text(json.dumps(seen_cache, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info(f"RSS seen cache : {len(new_seen)} nouvelle(s) URL(s) ajoutée(s), total {len(seen_cache)}")

    return all_items
