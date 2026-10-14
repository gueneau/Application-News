"""Flux Live — derniers articles RSS bruts, sans filtrage ni appel LLM.

Contrairement à abc_pipeline/sources/rss.py (qui filtre par mots-clés et
appelle Groq/Mistral pour scorer chaque article), ce script se contente de
lister les derniers éléments publiés par chaque flux RSS source, triés par
date de publication décroissante. Résultat écrit dans data/flux_live.json,
consommé par l'onglet "Flux Live" de index.html.

Usage :
    python -m scripts.flux_live
"""
import hashlib
import html
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from abc_pipeline.config import RSS_SOURCES, TIMEOUT  # noqa: E402

log = logging.getLogger(__name__)

MAX_PER_SOURCE = 6
MAX_TOTAL = 80
OUTPUT_PATH = Path(__file__).resolve().parent.parent / 'data' / 'flux_live.json'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}


def _clean_summary(entry) -> str:
    raw = (
        entry.get('summary')
        or entry.get('description')
        or (entry.get('content') or [{}])[0].get('value')
        or ''
    )
    text = html.unescape(re.sub(r'<[^>]+>', ' ', raw)).strip()
    text = re.sub(r'\s+', ' ', text)
    return text[:240]


def _entry_datetime(entry):
    pub = entry.get('published_parsed') or entry.get('updated_parsed')
    if not pub:
        return None
    try:
        return datetime.fromtimestamp(time.mktime(pub), tz=timezone.utc)
    except Exception:
        return None


def fetch_source(source: dict) -> list[dict]:
    url = source.get('url', '')
    if not url or 'invalid-no-rss' in url:
        return []

    try:
        resp = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning(f"Flux Live — {source['name']} : {e}")
        return []

    if feed.bozo and not feed.entries:
        log.warning(f"Flux Live — {source['name']} : flux invalide")
        return []

    items = []
    for entry in feed.entries[:MAX_PER_SOURCE]:
        titre = (entry.get('title') or '').strip()
        link = entry.get('link', '')
        if not titre or not link:
            continue

        dt = _entry_datetime(entry)
        items.append({
            'id': hashlib.sha1(f"{source['name']}|{link}".encode('utf-8')).hexdigest()[:16],
            'source': source['name'],
            'categorie': source.get('categorie', ''),
            'titre': titre,
            'resume': _clean_summary(entry),
            'url': link,
            'published_at': dt.isoformat() if dt else None,
        })

    log.info(f"Flux Live — {source['name']} : {len(items)} article(s)")
    return items


def main():
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    all_items = []
    for source in RSS_SOURCES:
        all_items.extend(fetch_source(source))

    # Tri par date décroissante ; les items sans date publiée passent en dernier.
    all_items.sort(key=lambda i: i['published_at'] or '', reverse=True)
    all_items = all_items[:MAX_TOTAL]

    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'items': all_items,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    log.info(f"Flux Live — {len(all_items)} article(s) écrit(s) dans {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
