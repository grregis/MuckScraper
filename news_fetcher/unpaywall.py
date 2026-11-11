"""
Unpaywall extraction and archive fallbacks for MuckScraper.
Pluggable scraper fallback that recovers readable article content for
paywalled, access-gated, or 401/402/403 articles via:
1. In-page structured metadata (JSON-LD articleBody fallback)
2. Social / Search referer pass (First-Click-Free emulation)
3. Internet Archive Wayback Machine (raw id_ snapshot with tracking params stripped)
4. Public Web Archives (Archive.today mirrors with captcha/Turnstile rejection)
"""

import logging
import json
import re
import requests
from urllib.parse import quote, urlparse, parse_qsl, urlencode, urlunparse
from bs4 import BeautifulSoup
from aggregator.html_safety import sanitize_html
from news_fetcher.scraper import (
    extract_with_readability,
    extract_structured_metadata,
    detect_bad_scrape,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

ARCHIVE_TODAY_MIRRORS = [
    "archive.today",
    "archive.ph",
    "archive.is",
    "archive.li",
]

WAYBACK_AVAILABILITY_API = "https://archive.org/wayback/available?url="
MIN_ARTICLE_LENGTH = 250

TRACKING_PARAM_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "taid", "ncid", "_hsenc", "_hsmi",
    "mc_cid", "mc_eid", "ito", "cmpid", "smid"
}


def clean_url_for_archives(url: str) -> str:
    """Strip marketing and analytics query parameters that cause archive cache misses."""
    try:
        parsed = urlparse(url)
        filtered = [
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAM_KEYS
        ]
        return urlunparse(parsed._replace(query=urlencode(filtered)))
    except Exception:
        return url


def make_wayback_raw_url(snapshot_url: str) -> str:
    """
    Transform a standard Wayback replay URL into a raw 'id_' snapshot URL.
    Example:
      /web/20261105181658/https://... -> /web/20261105181658id_/https://...
    This instructs Internet Archive to return the untouched source HTML without
    Wayback's injected toolbar, playback scripts (wombat.js), or banner CSS.
    """
    return re.sub(r'(/web/\d+)(?:[a-z_]+)?/', r'\1id_/', snapshot_url)


def extract_from_html_cascade(html: str, url: str) -> str | None:
    """
    Extract readable article text from raw HTML using a multi-engine cascade:
    1. Mozilla Readability
    2. Embedded structured metadata (JSON-LD / Schema.org articleBody)
    3. BeautifulSoup fallback for standard article containers
    """
    if not html:
        return None

    # 1. Readability
    content = extract_with_readability(html, url)
    if content and len(content) >= MIN_ARTICLE_LENGTH:
        return content

    # 2. Structured metadata (e.g. JSON-LD preserved in the snapshot)
    content = extract_structured_metadata(html, url)
    if content and len(content) >= MIN_ARTICLE_LENGTH:
        return content

    # 3. BeautifulSoup tag fallback
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "form"]):
            tag.decompose()

        article = soup.find("article")
        if article and len(article.get_text(strip=True)) >= MIN_ARTICLE_LENGTH:
            return sanitize_html(str(article))

        for selector in [
            {"class": "article-body"},
            {"class": "story-body"},
            {"class": "entry-content"},
            {"class": "post-content"},
            {"itemprop": "articleBody"},
            {"class": "body-text"},
        ]:
            found = soup.find(["div", "section"], selector)
            if found and len(found.get_text(strip=True)) >= MIN_ARTICLE_LENGTH:
                return sanitize_html(str(found))
    except Exception as e:
        logger.debug(f"[Unpaywall] Cascade extraction error on {url[:60]}: {e}")

    return None


def fetch_with_social_referer(url: str, timeout: int = 5) -> tuple[str | None, str | None]:
    """
    Attempt First-Click-Free access by simulating navigation from a search or social referer.
    Works for publishers that grant paywall exemptions to incoming search/social traffic.
    """
    referers = [
        ("https://www.google.com/", "google_referer"),
        ("https://t.co/", "social_referer"),
    ]
    for referer_url, label in referers:
        try:
            headers = {**HEADERS, "Referer": referer_url}
            r = requests.get(url, headers=headers, timeout=(3.0, timeout))
            if r.status_code == 200:
                is_bad, _ = detect_bad_scrape(r.text)
                if not is_bad:
                    content = extract_from_html_cascade(r.text, url)
                    if content and len(content) >= MIN_ARTICLE_LENGTH:
                        logger.info(f"  [Unpaywall] Recovered via {label}: {url[:60]}")
                        return content, label
        except Exception:
            continue
    return None, None


def fetch_wayback(url: str, timeout: int = 6) -> tuple[str | None, str | None]:
    """
    Query the Internet Archive Wayback Machine API for an existing snapshot.
    Uses stripped URLs for maximum cache hits and 'id_' modifier for clean raw HTML.
    """
    clean_url = clean_url_for_archives(url)
    try:
        api_url = WAYBACK_AVAILABILITY_API + quote(clean_url, safe=":/?=&%_-")
        r = requests.get(api_url, headers=HEADERS, timeout=(3.0, timeout))
        if r.status_code == 200:
            data = r.json()
            snap = data.get("archived_snapshots", {}).get("closest", {}).get("url")
            if snap:
                raw_snap = make_wayback_raw_url(snap)
                logger.info(f"  [Unpaywall] Found Wayback raw snapshot: {raw_snap[:80]}")
                snap_resp = requests.get(raw_snap, headers=HEADERS, timeout=(3.0, timeout + 4))
                if snap_resp.status_code == 200:
                    is_bad, _ = detect_bad_scrape(snap_resp.text)
                    if not is_bad:
                        content = extract_from_html_cascade(snap_resp.text, raw_snap)
                        if content and len(content) >= MIN_ARTICLE_LENGTH:
                            return content, "wayback_snapshot"
    except Exception as e:
        logger.debug(f"[Unpaywall] Wayback Machine lookup error: {e}")
    return None, None


def fetch_archive_today(url: str, timeout: int = 7) -> tuple[str | None, str | None]:
    """
    Query Archive.today mirrors for the newest snapshot of a URL.
    Filters out Cloudflare Turnstile challenge responses and checks multiple mirrors.
    """
    clean_url = clean_url_for_archives(url)
    for mirror in ARCHIVE_TODAY_MIRRORS[:2]:
        try:
            lookup_url = f"https://{mirror}/newest/{clean_url}"
            r = requests.get(lookup_url, headers=HEADERS, timeout=(3.0, timeout), allow_redirects=False)
            if r.status_code in (301, 302, 303, 307):
                snap_url = r.headers.get("Location")
                if snap_url and "/wip/" not in snap_url:
                    if snap_url.startswith("/"):
                        snap_url = f"https://{mirror}{snap_url}"
                    logger.info(f"  [Unpaywall] Found {mirror} snapshot: {snap_url[:80]}")
                    snap_resp = requests.get(snap_url, headers=HEADERS, timeout=(3.0, timeout + 3))
                    if snap_resp.status_code == 200:
                        is_bad, _ = detect_bad_scrape(snap_resp.text)
                        if is_bad:
                            logger.info(f"  [Unpaywall] {mirror} snapshot was a challenge/captcha page, skipping")
                            continue
                        content = extract_from_html_cascade(snap_resp.text, snap_url)
                        if content and len(content) >= MIN_ARTICLE_LENGTH:
                            return content, f"{mirror}_snapshot"
        except Exception as e:
            logger.debug(f"[Unpaywall] Error probing {mirror}: {e}")
            continue
    return None, None


def unpaywall_article(url: str, initial_html: str | None = None) -> tuple[str | None, str | None]:
    """
    Plugin entry point: Multi-stage unpaywall and archive recovery pipeline.
    1. In-page structured metadata (JSON-LD from original HTML if passed)
    2. First-Click-Free referer pass (fast ~200ms check for Google/Twitter referer gates)
    3. Internet Archive Wayback Machine (raw id_ snapshot with stripped tracking parameters)
    4. Archive.today mirrors (with Turnstile challenge rejection)
    Returns (sanitized_html, method_name) or (None, None).
    """
    # Stage 1: In-page structured metadata if initial_html is available
    if initial_html:
        json_ld_body = extract_structured_metadata(initial_html, url)
        if json_ld_body and len(json_ld_body) >= MIN_ARTICLE_LENGTH:
            logger.info(f"  [Unpaywall] Extracted JSON-LD structured body ({len(json_ld_body)} chars) from original HTML")
            return json_ld_body, "json_ld_metadata"

    # Stage 2: First-Click-Free social / search referer
    content, method = fetch_with_social_referer(url)
    if content:
        return content, method

    # Stage 3: Internet Archive Wayback Machine (raw snapshot)
    content, method = fetch_wayback(url)
    if content:
        return content, method

    # Stage 4: Archive.today mirrors
    content, method = fetch_archive_today(url)
    if content:
        return content, method

    return None, None
