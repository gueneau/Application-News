import json
import logging
import re

import requests

from .config import TIMEOUT

log = logging.getLogger(__name__)

_image_cache: dict = {}
_date_cache:  dict = {}

# Marqueurs de class/id des bandeaux de consentement cookies / paywall les plus
# courants sur la presse FR — jamais du contenu d'article, mais pas couverts
# par le strip nav/header/footer/aside (souvent de simples <div> injectés en
# tête de page par la CMP). Sans ce filtre, ce bandeau peut occuper toute la
# tranche de texte envoyée au LLM et masquer entièrement le vrai article.
_BOILERPLATE_MARKERS = (
    'cookie', 'consent', 'rgpd', 'gdpr', 'cmp-', 'paywall',
    'didomi', 'tarteaucitron', 'axeptio', 'onetrust', 'cookiebot', 'quantcast',
)


def _strip_boilerplate(soup) -> None:
    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
        tag.decompose()
    for tag in soup.find_all(True):
        # Un decompose() plus haut dans l'arbre peut avoir déjà détaché ce tag
        # (toujours présent dans ce snapshot de find_all) ; ses attrs deviennent alors None.
        if tag.attrs is None:
            continue
        marker_text = f"{' '.join(tag.get('class') or [])} {tag.get('id') or ''}".lower()
        if any(marker in marker_text for marker in _BOILERPLATE_MARKERS):
            tag.decompose()


def get_crawled_image(url: str) -> str:
    """Return the og:image URL cached during the last crawl_article() call for this URL."""
    return _image_cache.get(url, '')


def get_crawled_date(url: str) -> str:
    """Return the publication date (YYYY-MM-DD) cached during the last crawl_article() call, or ''."""
    return _date_cache.get(url, '')


def _extract_date_from_soup(soup) -> str:
    """Try to extract a publication date from common HTML patterns. Returns 'YYYY-MM-DD' or ''."""
    # 1. Open Graph / meta article:published_time
    for attr in ({'property': 'article:published_time'}, {'name': 'date'}, {'name': 'DC.date'}, {'itemprop': 'datePublished'}):
        tag = soup.find('meta', attrs=attr)
        if tag and tag.get('content'):
            m = re.search(r'(\d{4}-\d{2}-\d{2})', tag['content'])
            if m:
                return m.group(1)

    # 2. <time datetime="YYYY-MM-DD..."> (first one found)
    for t in soup.find_all('time', datetime=True):
        m = re.search(r'(\d{4}-\d{2}-\d{2})', t['datetime'])
        if m:
            return m.group(1)

    # 3. JSON-LD datePublished
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            # Handle both single object and list
            items = data if isinstance(data, list) else [data]
            for item in items:
                dp = item.get('datePublished') or item.get('dateCreated') or ''
                m = re.search(r'(\d{4}-\d{2}-\d{2})', dp)
                if m:
                    return m.group(1)
        except Exception:
            pass

    return ''


def crawl_article(url: str, max_chars: int | None = 2500) -> str:
    """Fetch textual content of an URL via BeautifulSoup.
    Also caches og:image and publication date, retrievable via get_crawled_image() / get_crawled_date().
    Set max_chars=None to return the full extracted text.
    """
    try:
        from bs4 import BeautifulSoup

        resp = requests.get(url, timeout=TIMEOUT, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'fr-FR,fr;q=0.9',
        })
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        og = (
            soup.find('meta', property='og:image')
            or soup.find('meta', attrs={'name': 'twitter:image'})
        )
        if og and og.get('content', '').strip().startswith('http'):
            _image_cache[url] = og['content'].strip()

        pub_date = _extract_date_from_soup(soup)
        if pub_date:
            _date_cache[url] = pub_date

        _strip_boilerplate(soup)

        text = soup.get_text(separator=' ', strip=True)
        return text if max_chars is None else text[:max_chars]
    except Exception as e:
        log.debug(f"Crawl error {url}: {e}")
        return ''


def crawl_article_links(listing_url: str, base_url: str, max_links: int = 5) -> list:
    """
    Crawl a listing page and extract links to individual articles.
    Returns a list of dicts: {titre, url}.
    """
    try:
        from bs4 import BeautifulSoup

        resp = requests.get(listing_url, timeout=TIMEOUT, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36', 'Accept-Language': 'fr-FR,fr;q=0.9'})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        links = []
        seen = set()

        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            titre = a.get_text(strip=True)

            if not titre or len(titre) < 15:
                continue
            if any(
                skip in href
                for skip in [
                    '#', 'mailto:', 'javascript:', '/tag/', '/category/',
                    '/page/', '/feed', '/rss', '/newsletter', '/contact',
                    '/about', '/team', '/equipe', '/linstitut',
                ]
            ):
                continue

            if href.startswith('http'):
                full_url = href
            elif href.startswith('/'):
                full_url = base_url.rstrip('/') + href
            else:
                continue

            if full_url in seen:
                continue
            seen.add(full_url)

            # Keep only same domain
            if base_url.split('/')[2] not in full_url:
                continue

            links.append({'titre': titre[:200], 'url': full_url})
            if len(links) >= max_links:
                break

        return links
    except Exception as e:
        log.debug(f"crawl_article_links error {listing_url}: {e}")
        return []


def crawl_playwright_links(url: str, max_links: int = 15) -> list:
    """
    Use Playwright (headless Chromium) to scrape a JS-rendered page and extract article links.
    Returns a list of dicts: {titre, url}.
    Used exclusively for sources that cannot be scraped with requests (e.g. Google News publications).
    """
    try:
        from urllib.parse import urljoin
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page.goto(url, timeout=30000, wait_until='networkidle')
            try:
                page.wait_for_selector('article', timeout=15000)
            except Exception:
                pass

            links = []
            seen = set()
            articles = page.query_selector_all('article')
            for art in articles:
                # Title: prefer h3/h4 heading inside the article block
                heading = art.query_selector('h3, h4')
                titre = heading.inner_text().strip() if heading else ''
                # Link: first <a> with an href
                a_tag = art.query_selector('a[href]')
                if not a_tag:
                    continue
                if not titre:
                    titre = a_tag.inner_text().strip()
                href = a_tag.get_attribute('href') or ''
                if not titre or len(titre) < 10 or not href:
                    continue
                full_url = urljoin(url, href)
                if full_url in seen:
                    continue
                seen.add(full_url)
                links.append({'titre': titre[:200], 'url': full_url})
                if len(links) >= max_links:
                    break

            browser.close()
            return links

    except Exception as e:
        log.debug(f"crawl_playwright_links error {url}: {e}")
        return []


def crawl_article_links_filtered(listing_url: str, base_url: str, url_contains: str, max_links: int = 8) -> list:
    """Like crawl_article_links but only keeps links whose URL contains `url_contains`."""
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(listing_url, timeout=TIMEOUT, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36', 'Accept-Language': 'fr-FR,fr;q=0.9'})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        seen = set()
        for a in soup.find_all('a', href=True):
            href = a['href'].strip()
            titre = a.get_text(strip=True)
            if not titre or len(titre) < 15:
                continue
            full_url = href if href.startswith('http') else base_url.rstrip('/') + href if href.startswith('/') else None
            if not full_url or full_url in seen:
                continue
            if base_url.split('/')[2] not in full_url:
                continue
            if url_contains not in full_url:
                continue
            seen.add(full_url)
            links.append({'titre': titre[:200], 'url': full_url})
            if len(links) >= max_links:
                break
        return links
    except Exception as e:
        log.debug(f"crawl_article_links_filtered error {listing_url}: {e}")
        return []
