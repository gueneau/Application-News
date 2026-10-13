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
from ..filters import keyword_match, make_id, valid_category
from ..llm import groq_analyse_rss

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


def fetch_rss_source(source: dict, today_str: str, seen_urls: dict = None, new_seen: dict = None):
    items = []
    name = source['name']

    # ── Route Playwright : sources JS-rendered (ex: Google News publication) ──
    if source.get('playwright_crawl'):
        playwright_url = source['playwright_crawl']
        log.info(f"Playwright → {playwright_url}")
        try:
            article_links = crawl_playwright_links(playwright_url, max_links=15)
            log.info(f"Playwright {name} : {len(article_links)} liens extraits")
            now = datetime.now()
            fallback_cutoff = (
                (now - timedelta(days=3)).replace(hour=18, minute=0, second=0, microsecond=0)
                if now.weekday() == 0
                else now - timedelta(hours=24)
            )
            for link in article_links:
                try:
                    if seen_urls is not None and link['url'] in seen_urls:
                        continue
                    titre_art = link['titre']
                    title_dt = _date_from_text(titre_art)
                    titre_art = re.sub(r'\s*\d{1,2}/\d{2}/\d{4}.*$', '', titre_art).strip()
                    if not titre_art or len(titre_art) < 10:
                        continue
                    if title_dt and title_dt < fallback_cutoff:
                        log.debug(f"Playwright date titre trop ancienne ({title_dt.date()}), exclu : {titre_art[:60]}")
                        continue

                    contenu = crawl_article(link['url']) or ''
                    txt = titre_art + ' ' + contenu

                    if source.get('require_keywords'):
                        if not any(k.lower() in txt.lower() for k in source['require_keywords']):
                            continue
                    if not keyword_match(txt):
                        continue

                    analysis = groq_analyse_rss(titre_art, contenu)
                    if analysis.get('pertinent') is False:
                        continue

                    score = int(analysis.get('score') or 1)
                    pourquoi = analysis.get('pourquoi', '') if score >= 2 else ''
                    article_date = get_crawled_date(link['url']) or today_str

                    if new_seen is not None:
                        new_seen[link['url']] = today_str

                    items.append({
                        'id': make_id(name, titre_art),
                        'source': name,
                        'categorie': valid_category(analysis.get('categorie')),
                        'titre': titre_art,
                        'resume': analysis.get('resume') or titre_art,
                        'pourquoi': pourquoi,
                        'criticite': score,
                        'url': link['url'],
                        'date': article_date,
                    })
                except Exception as e_art:
                    log.debug(f"Playwright article {link['url']} : {e_art}")
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

            now = datetime.now()
            if now.weekday() == 0:
                cutoff_dt = (now - timedelta(days=3)).replace(
                    hour=18, minute=0, second=0, microsecond=0
                )
            else:
                cutoff_dt = now - timedelta(hours=24)

            if article_dt is None or article_dt < cutoff_dt:
                log.debug(f"Article sans date ou trop ancien ({article_date}), exclu : {titre[:60]}")
                continue

            # Certains flux (ex: Carbone 4) publient pubDate=aujourd'hui sur de vieux articles.
            # Si une date est lisible dans le titre et qu'elle est ancienne → exclure.
            title_dt = _date_from_text(titre)
            if title_dt and title_dt < cutoff_dt:
                log.debug(f"Date dans le titre trop ancienne ({title_dt.date()}), exclu : {titre[:60]}")
                continue

            if len(contenu) < 100 and url:
                contenu = crawl_article(url) or contenu

            if source.get('require_keywords'):
                txt = (titre + ' ' + contenu).lower()
                if not any(k.lower() in txt for k in source['require_keywords']):
                    continue

            if not keyword_match(titre + ' ' + contenu):
                continue

            analysis = groq_analyse_rss(titre, contenu)
            if analysis.get('pertinent') is False:
                log.debug(f"Non pertinent ABC, exclu : {titre[:60]}")
                continue

            score = int(analysis.get('score', 1))
            pourquoi = analysis.get('pourquoi', '')
            if score < 2:
                pourquoi = ''

            items.append({
                'id': make_id(name, titre),
                'source': name,
                'categorie': valid_category(analysis.get('categorie')),
                'titre': titre,
                'resume': analysis.get('resume') or titre,
                'pourquoi': pourquoi,
                'criticite': score,
                'url': url,
                'date': article_date,
            })

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
                    if contenu and keyword_match(contenu):
                        analysis = groq_analyse_rss(f"Actualités {name}", contenu)
                        if analysis.get('pertinent') is not False:
                            items.append({
                                'id': make_id(name, 'fallback'),
                                'source': name,
                                'categorie': 'Presse',
                                'titre': f'Actualités {name}',
                                'resume': analysis.get('resume') or 'Source consultée via fallback.',
                                'pourquoi': '',
                                'criticite': 1,
                                'url': fallback_url,
                                'date': today_str,
                            })
                else:
                    now = datetime.now()
                    fallback_cutoff = (now - timedelta(days=3)).replace(hour=18, minute=0, second=0, microsecond=0) if now.weekday() == 0 else now - timedelta(hours=24)
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

                            contenu = crawl_article(link['url']) or ''
                            # Paywalled/inaccessible → use title only
                            txt = titre_art + ' ' + contenu

                            # The publication date wins over dates mentioned in the headline.
                            html_date_str = get_crawled_date(link['url'])
                            if html_date_str:
                                try:
                                    html_dt = datetime.strptime(html_date_str, '%Y-%m-%d')
                                    if html_dt < fallback_cutoff:
                                        log.debug(f"Fallback date HTML trop ancienne ({html_date_str}), exclu : {titre_art[:60]}")
                                        continue
                                except ValueError:
                                    pass

                            if source.get('require_keywords'):
                                if not any(k.lower() in txt.lower() for k in source['require_keywords']):
                                    continue

                            if not keyword_match(txt):
                                continue

                            analysis = groq_analyse_rss(titre_art, contenu)
                            if analysis.get('pertinent') is False:
                                continue

                            score = int(analysis.get('score') or 1)
                            pourquoi = analysis.get('pourquoi', '') if score >= 2 else ''

                            # Use date extracted from article HTML if available, else today
                            article_date = get_crawled_date(link['url']) or today_str

                            # Mark URL as seen
                            if new_seen is not None:
                                new_seen[link['url']] = today_str

                            items.append({
                                'id': make_id(name, titre_art),
                                'source': name,
                                'categorie': valid_category(analysis.get('categorie')),
                                'titre': titre_art,
                                'resume': analysis.get('resume') or titre_art,
                                'pourquoi': pourquoi,
                                'criticite': score,
                                'url': link['url'],
                                'date': article_date,
                            })

                        except Exception as e_art:
                            log.debug(f"Fallback article {link['url']} : {e_art}")

            except Exception as e2:
                log.warning(f"Fallback {name} error : {e2}")

    return items


def fetch_rss(today_str: str, script_dir: Path = None):
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

    all_items = []
    for source in RSS_SOURCES:
        is_fallback = bool(source.get('fallback_crawl') or source.get('playwright_crawl'))
        items = fetch_rss_source(source, today_str, seen_urls=seen_cache if is_fallback else None, new_seen=new_seen if is_fallback else None)
        if not items:
            log.warning(f"SOURCE VIDE : {source['name']} — 0 articles retenus")
        all_items.extend(items)
        log.info(f"RSS {source['name']} : {len(items)} retenus")

    # Save updated seen cache
    if seen_path is not None and new_seen:
        seen_cache.update(new_seen)
        seen_path.parent.mkdir(parents=True, exist_ok=True)
        seen_path.write_text(json.dumps(seen_cache, ensure_ascii=False, indent=2), encoding='utf-8')
        log.info(f"RSS seen cache : {len(new_seen)} nouvelle(s) URL(s) ajoutée(s), total {len(seen_cache)}")

    return all_items
