# muckscraperHeadlinesGoogleNEW/news_fetcher/scraper.py
# news_fetcher/scraper.py

import requests
from bs4 import BeautifulSoup
import bleach
import time
import os
import logging
from difflib import SequenceMatcher
import re
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
logger = logging.getLogger(__name__)

HEADERS_DEFAULT = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

HEADERS_GOOGLEBOT = {
    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
}

ALLOWED_TAGS = [
    "p", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "u",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
]

ALLOWED_ATTRIBUTES = {
    "a":   ["href", "title"],
    "img": ["src", "alt", "title"],
    "td":  ["colspan", "rowspan"],
    "th":  ["colspan", "rowspan"],
}

# Sites that need Playwright (heavy JS)
PLAYWRIGHT_DOMAINS = [
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "nytimes.com",
    "washingtonpost.com",
    "theathletic.com",
    "wired.com",
]

# Sites to try Googlebot user agent on
GOOGLEBOT_DOMAINS = [
    "axios.com",
    "politico.com",
    "theatlantic.com",
    "thedailybeast.com",
    "businessinsider.com",
    "sfgate.com",
    "telegraph.co.uk",
]

VARIANT_RETRY_403_DOMAINS = [
    "thehill.com",
]

# Sites to skip entirely
SKIP_DOMAINS = [
    "youtube.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
]

STRONG_BAD_SCRAPE_INDICATORS = [
    "unusual activity detected",
    "verify you are human",
    "enable javascript to continue",
    "you have been blocked",
    "access to this page has been denied",
    "please sign in to continue",
    "subscribe to continue reading",
    "please verify you're not a robot",
    "complete the security check",
    "captcha",
    "403 forbidden",
    "this content is for subscribers",
    "create a free account to read",
    "sign up to read",
    "your access to this article",
    "to continue reading, please",
    "this article is for paying subscribers",
]

WEAK_BAD_SCRAPE_INDICATORS = [
    "sign in",
    "log in",
    "subscribe",
    "premium content",
]

SCRAPE_STATUS_SUCCESS = "success"
SCRAPE_STATUS_FALLBACK = "fallback"
SCRAPE_STATUS_BLOCKED = "blocked"
SCRAPE_STATUS_SKIPPED = "skipped"
SCRAPE_STATUS_FAILED = "failed"
RETRY_CACHE_SETTING_KEY = "scrape_retry_cache_v1"
RETRY_CACHE_MAX_ENTRIES = 500


@dataclass
class ScrapeResult:
    content: str | None
    status: str
    method: str | None = None
    failure_reason: str | None = None
    http_status: int | None = None

    @property
    def succeeded(self):
        return bool(self.content)


def get_domain(url):
    """Extract bare domain from a URL, stripping www."""
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return None


def normalize_retry_cache_url(url):
    """Normalize a URL for retry-cache lookups by stripping the query string."""
    try:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    except Exception:
        return url


def _utcnow():
    return datetime.utcnow()


def _empty_retry_cache():
    return {"domains": {}, "urls": {}}


def _load_retry_cache():
    try:
        from flask import has_app_context
        if not has_app_context():
            return _empty_retry_cache()

        from aggregator.models import AppSetting
        setting = AppSetting.query.filter_by(key=RETRY_CACHE_SETTING_KEY).first()
        if not setting or not setting.value:
            return _empty_retry_cache()

        payload = json.loads(setting.value)
        if not isinstance(payload, dict):
            return _empty_retry_cache()
        payload.setdefault("domains", {})
        payload.setdefault("urls", {})
        return payload
    except Exception as exc:
        logger.info(f"  [Scraper] Retry cache load skipped: {exc}")
        return _empty_retry_cache()


def _save_retry_cache(cache):
    try:
        from flask import has_app_context
        if not has_app_context():
            return

        from aggregator import db
        from aggregator.models import AppSetting

        payload = json.dumps(cache, sort_keys=True)
        setting = AppSetting.query.filter_by(key=RETRY_CACHE_SETTING_KEY).first()
        if setting:
            setting.value = payload
        else:
            db.session.add(AppSetting(key=RETRY_CACHE_SETTING_KEY, value=payload))
        db.session.commit()
    except Exception as exc:
        logger.info(f"  [Scraper] Retry cache save skipped: {exc}")


def _prune_retry_cache(cache, now=None):
    now = now or _utcnow()
    now_iso = now.isoformat()

    for scope in ("domains", "urls"):
        scope_cache = cache.get(scope, {})
        expired = [
            key for key, entry in scope_cache.items()
            if not isinstance(entry, dict) or entry.get("defer_until", "") <= now_iso
        ]
        for key in expired:
            scope_cache.pop(key, None)

        if len(scope_cache) > RETRY_CACHE_MAX_ENTRIES:
            ranked = sorted(
                scope_cache.items(),
                key=lambda item: item[1].get("updated_at", ""),
                reverse=True,
            )
            cache[scope] = dict(ranked[:RETRY_CACHE_MAX_ENTRIES])

    return cache


def _build_retry_cache_entry(status, failure_reason, backoff_seconds, failure_count, now):
    return {
        "status": status,
        "failure_reason": failure_reason,
        "failure_count": failure_count,
        "updated_at": now.isoformat(),
        "defer_until": (now + timedelta(seconds=backoff_seconds)).isoformat(),
    }


def _backoff_seconds_for_result(result, failure_count, scope):
    failure_reason = (result.failure_reason or "").lower()
    http_status = result.http_status

    if result.status == SCRAPE_STATUS_BLOCKED or "domain_blocked" in failure_reason:
        return 24 * 3600 if scope == "domains" else 12 * 3600

    if http_status == 429:
        return (45 * 60 * failure_count) if scope == "urls" else (20 * 60 * max(1, failure_count - 1))

    if http_status in (401, 403):
        return (8 * 3600 * failure_count) if scope == "urls" else (6 * 3600 * max(1, failure_count - 1))

    if http_status in (404, 410):
        return 12 * 3600 if scope == "urls" else 0

    if http_status and http_status >= 500:
        return (20 * 60 * failure_count) if scope == "urls" else (10 * 60 * max(1, failure_count - 1))

    if "timeout" in failure_reason or "timed out" in failure_reason:
        return (15 * 60 * failure_count) if scope == "urls" else (10 * 60 * max(1, failure_count - 1))

    if "extraction_failed" in failure_reason:
        return (2 * 3600 * failure_count) if scope == "urls" else (60 * 60 * max(1, failure_count - 2))

    return (60 * 60 * failure_count) if scope == "urls" else (30 * 60 * max(1, failure_count - 2))


def _should_cache_retry_result(result):
    if result.status in (SCRAPE_STATUS_SUCCESS, SCRAPE_STATUS_FALLBACK, SCRAPE_STATUS_SKIPPED):
        return False
    return True


def update_retry_cache(url, result, now=None):
    """Persist retry cooldowns based on scrape telemetry."""
    now = now or _utcnow()
    cache = _prune_retry_cache(_load_retry_cache(), now=now)
    domain = get_domain(url)
    normalized_url = normalize_retry_cache_url(url)

    if result.status == SCRAPE_STATUS_SUCCESS:
        if domain:
            cache["domains"].pop(domain, None)
        cache["urls"].pop(normalized_url, None)
        _save_retry_cache(cache)
        return

    if result.status == SCRAPE_STATUS_FALLBACK:
        cache["urls"].pop(normalized_url, None)
        _save_retry_cache(cache)
        return

    if not _should_cache_retry_result(result):
        return

    url_entry = cache["urls"].get(normalized_url, {})
    url_failure_count = int(url_entry.get("failure_count", 0)) + 1
    url_backoff = _backoff_seconds_for_result(result, url_failure_count, "urls")
    if url_backoff > 0:
        cache["urls"][normalized_url] = _build_retry_cache_entry(
            result.status,
            result.failure_reason,
            url_backoff,
            url_failure_count,
            now,
        )

    if domain:
        domain_entry = cache["domains"].get(domain, {})
        domain_failure_count = max(
            int(domain_entry.get("failure_count", 0)) + 1,
            sum(
                max(1, int(entry.get("failure_count", 1)))
                for cached_url, entry in cache["urls"].items()
                if get_domain(cached_url) == domain
            ),
        )
        domain_backoff = _backoff_seconds_for_result(result, domain_failure_count, "domains")
        if domain_backoff > 0 and (
            result.status == SCRAPE_STATUS_BLOCKED or domain_failure_count >= 3 or result.http_status in (401, 403, 429)
        ):
            cache["domains"][domain] = _build_retry_cache_entry(
                result.status,
                result.failure_reason,
                domain_backoff,
                domain_failure_count,
                now,
            )

    _save_retry_cache(_prune_retry_cache(cache, now=now))


def get_retry_deferral(url, now=None):
    """Return an active retry deferral for this URL or domain, if any."""
    now = now or _utcnow()
    cache = _prune_retry_cache(_load_retry_cache(), now=now)
    domain = get_domain(url)
    normalized_url = normalize_retry_cache_url(url)
    now_iso = now.isoformat()

    url_entry = cache.get("urls", {}).get(normalized_url)
    if isinstance(url_entry, dict) and url_entry.get("defer_until", "") > now_iso:
        return "url", url_entry

    if domain:
        domain_entry = cache.get("domains", {}).get(domain)
        if isinstance(domain_entry, dict) and domain_entry.get("defer_until", "") > now_iso:
            return "domain", domain_entry

    return None, None


def should_try_variant_urls(url=None, http_status=None, failure_reason=None):
    """Return False when the base failure is stable enough that variants are low-value."""
    domain = get_domain(url) if url else None
    if domain in VARIANT_RETRY_403_DOMAINS and http_status in (403,):
        return True

    if http_status in (401, 403, 404, 410):
        return False

    failure_reason = (failure_reason or "").lower()
    if failure_reason in {"http_401", "http_403", "http_404", "http_410"}:
        return False

    return True


def should_auto_rescrape_article(article, minimum_content_length=500):
    """Return True when this article is a good candidate for bulk auto-rescrape."""
    status = (getattr(article, "scrape_status", None) or "pending").lower()
    content = getattr(article, "content", None) or ""
    content_length = len(_clean_text(re.sub(r"<[^>]+>", " ", content)))

    if status in (SCRAPE_STATUS_BLOCKED, SCRAPE_STATUS_SKIPPED):
        return False
    if status in ("pending", SCRAPE_STATUS_FAILED):
        return True
    if status in (SCRAPE_STATUS_SUCCESS, SCRAPE_STATUS_FALLBACK):
        return content_length < minimum_content_length
    return not content or content_length < minimum_content_length


def is_domain_blocked(url):
    """Return True if this URL's domain is on the scrape blocklist."""
    try:
        from aggregator.models import ScrapeBlocklist
        domain = get_domain(url)
        if not domain:
            return False
        return ScrapeBlocklist.query.filter_by(domain=domain).first() is not None
    except Exception:
        return False


def add_to_blocklist(url, reason, is_permanent=False):
    """Add a domain to the scrape blocklist. Silent no-op if already present."""
    try:
        from aggregator import db
        from aggregator.models import ScrapeBlocklist
        from datetime import datetime
        domain = get_domain(url)
        if not domain:
            return
        existing = ScrapeBlocklist.query.filter_by(domain=domain).first()
        if existing:
            return
        entry = ScrapeBlocklist(
            domain=domain,
            reason=reason,
            is_permanent=is_permanent,
            added_at=datetime.utcnow(),
        )
        db.session.add(entry)
        db.session.commit()
        logger.info(f"[Blocklist] Added {domain}: {reason}")
    except Exception as e:
        logger.warning(f"[Blocklist] Failed to add domain: {e}")


def detect_bad_scrape(content):
    """
    Check scraped content for signs of a login wall, captcha, or bot-detection page.
    Returns (is_bad: bool, reason: str or None).
    """
    if not content:
        return False, None

    # Strip HTML and collapse whitespace for clean comparison
    clean = re.sub(r'<[^>]+>', ' ', content)
    clean = re.sub(r'\s+', ' ', clean).strip().lower()

    for indicator in STRONG_BAD_SCRAPE_INDICATORS:
        if indicator in clean:
            return True, f"Bad scrape: strong indicator '{indicator}'"

    if len(clean) < 300:
        for indicator in WEAK_BAD_SCRAPE_INDICATORS:
            if indicator in clean:
                return True, f"Bad scrape: weak indicator '{indicator}' in short content ({len(clean)} chars)"

    return False, None


def should_skip(url):
    return any(domain in url.lower() for domain in SKIP_DOMAINS)


def needs_playwright(url):
    return any(domain in url.lower() for domain in PLAYWRIGHT_DOMAINS)


def use_googlebot(url):
    return any(domain in url.lower() for domain in GOOGLEBOT_DOMAINS)


def sanitize_html(raw_html):
    return bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True,
    )


def _fetch_html(url, headers=None, timeout=10):
    if headers is None:
        headers = HEADERS_DEFAULT
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response


def _clean_text(text):
    return re.sub(r'\s+', ' ', (text or '')).strip()


def extract_with_readability(html, url):
    """
    Use Mozilla's readability algorithm to extract main article content.
    Returns sanitized HTML or None.
    """
    try:
        from readability import Document
        doc = Document(html)
        content = doc.summary()
        if content and len(content) > 200:
            sanitized = sanitize_html(content)
            if len(sanitized) > 200:
                logger.info(f"  [Readability] Extracted {len(sanitized)} chars from {url[:60]}")
                return sanitized
    except Exception as e:
        logger.info(f"  [Readability] Error: {e}")
    return None


def extract_structured_metadata(html, url):
    """
    Extract article-ish text from JSON-LD, OpenGraph, Twitter cards, and
    schema.org/article meta tags. Returns sanitized HTML or None.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        logger.info(f"  [Metadata] Parse error for {url[:60]}: {e}")
        return None

    candidates = []

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        stack = payload if isinstance(payload, list) else [payload]
        for item in stack:
            if not isinstance(item, dict):
                continue
            body = item.get("articleBody") or item.get("description")
            headline = item.get("headline")
            if body:
                candidates.append((headline, body))

    meta_fields = [
        ("meta", {"property": "og:description"}),
        ("meta", {"name": "twitter:description"}),
        ("meta", {"name": "description"}),
        ("meta", {"itemprop": "description"}),
        ("meta", {"itemprop": "articleBody"}),
    ]
    for tag_name, attrs in meta_fields:
        tag = soup.find(tag_name, attrs=attrs)
        if not tag:
            continue
        value = tag.get("content") or tag.get_text()
        if value:
            candidates.append((None, value))

    best_html = None
    best_len = 0
    for headline, body in candidates:
        clean_body = _clean_text(body)
        if len(clean_body) < 140:
            continue
        parts = []
        if headline:
            parts.append(f"<h2>{bleach.clean(headline, strip=True)}</h2>")
        parts.append(f"<p>{bleach.clean(clean_body, strip=True)}</p>")
        candidate_html = sanitize_html("<div>" + "".join(parts) + "</div>")
        if len(candidate_html) > best_len:
            best_html = candidate_html
            best_len = len(candidate_html)

    if best_html:
        logger.info(f"  [Metadata] Extracted {best_len} chars from {url[:60]}")
    return best_html


def build_variant_urls(url, html=None):
    """
    Generate likely article variants without crossing origin boundaries.
    """
    variants = []
    seen = set()

    def add(candidate):
        if not candidate or candidate == url or candidate in seen:
            return
        seen.add(candidate)
        variants.append(candidate)

    if html:
        try:
            soup = BeautifulSoup(html, "html.parser")
            canonical = soup.find("link", rel=lambda value: value and "canonical" in value.lower())
            if canonical and canonical.get("href"):
                add(urljoin(url, canonical["href"]))

            amp = soup.find("link", rel=lambda value: value and "amphtml" in value.lower())
            if amp and amp.get("href"):
                add(urljoin(url, amp["href"]))
        except Exception:
            pass

    parsed = urlparse(url)
    query_pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    domain = get_domain(url)

    if domain == "thehill.com" and not parsed.path.rstrip("/").endswith("/amp"):
        add(urlunparse(parsed._replace(path=parsed.path.rstrip("/") + "/amp/")))

    if "output" not in query_pairs:
        q = dict(query_pairs)
        q["output"] = "amp"
        add(urlunparse(parsed._replace(query=urlencode(q, doseq=True))))

    if "amp" not in query_pairs:
        q = dict(query_pairs)
        q["amp"] = "1"
        add(urlunparse(parsed._replace(query=urlencode(q, doseq=True))))

    if "mobile" not in query_pairs:
        q = dict(query_pairs)
        q["mobile"] = "1"
        add(urlunparse(parsed._replace(query=urlencode(q, doseq=True))))

    if not parsed.path.endswith("/amp"):
        add(urlunparse(parsed._replace(path=parsed.path.rstrip("/") + "/amp")))
    if not parsed.path.endswith("/print"):
        add(urlunparse(parsed._replace(path=parsed.path.rstrip("/") + "/print")))

    host = parsed.netloc
    if host.startswith("www."):
        add(urlunparse(parsed._replace(netloc="m." + host[4:])))

    return variants


def extract_article_html_bs4(url, headers=None):
    """
    Scrape article using BS4 and return sanitized HTML string.
    Falls back to readability if direct extraction fails.
    """
    if headers is None:
        headers = HEADERS_DEFAULT
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html = response.text

        # Try readability first — it's smarter than manual selectors
        content = extract_with_readability(html, url)
        if content:
            return content

        # Fall back to manual BS4 extraction
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "nav", "header", "footer",
                         "aside", "advertisement", "figure", "figcaption",
                         "iframe", "noscript", "button", "form"]):
            tag.decompose()

        content_html = None

        article = soup.find("article")
        if article:
            content_html = str(article)

        if not content_html or len(content_html) < 200:
            for selector in [
                {"class": "article-body"},
                {"class": "article-content"},
                {"class": "story-body"},
                {"class": "story-content"},
                {"class": "post-content"},
                {"class": "entry-content"},
                {"class": "content-body"},
                {"id": "article-body"},
                {"id": "story-body"},
                {"itemprop": "articleBody"},
                {"class": "body-text"},
            ]:
                found = soup.find(["div", "section"], selector)
                if found and len(found.get_text(strip=True)) > 200:
                    content_html = str(found)
                    break

        if not content_html or len(content_html) < 200:
            paragraphs = soup.find_all("p")
            if paragraphs:
                combined = "".join(str(p) for p in paragraphs)
                if len(combined) > 200:
                    content_html = f"<div>{combined}</div>"

        if content_html and len(content_html) > 200:
            sanitized = sanitize_html(content_html)
            logger.info(f"  [BS4] Scraped {len(sanitized)} chars from {url[:60]}")
            return sanitized

        logger.info(f"  [BS4] Could not extract sufficient content from {url[:60]}")
        return None

    except Exception as e:
        logger.info(f"  [BS4] Error scraping {url[:60]}: {e}")
        return None


def extract_article_html_playwright(url):
    """
    Scrape article using Playwright and return sanitized HTML string.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers(HEADERS_DEFAULT)

            page.goto(url, timeout=15000, wait_until="domcontentloaded")
            time.sleep(2)

            page.evaluate("""
                ['script','style','nav','header','footer','aside',
                 'iframe','button','form']
                .forEach(tag => document.querySelectorAll(tag)
                .forEach(el => el.remove()))
            """)

            html = page.content()
            browser.close()

            # Try readability on the rendered HTML first
            content = extract_with_readability(html, url)
            if content:
                return content

            # Manual JS fallback removed 2026-09-08: `browser.close()` above
            # already ran by this point, so page.evaluate() here would fail
            # on a dead page -- this had been silently short-circuited to
            # None via `if False` rather than fixed or removed. Readability
            # above is this function's real extraction path. A BS4-based
            # fallback mirroring the one in extract_article_html_bs4() would
            # need to operate on `html`, not a closed page, if this ever
            # needs its own last-resort fallback.
            content_html = None

            if content_html and len(content_html) > 200:
                sanitized = sanitize_html(content_html)
                logger.info(f"  [Playwright] Scraped {len(sanitized)} chars from {url[:60]}")
                return sanitized

            logger.info(f"  [Playwright] Could not extract content from {url[:60]}")
            return None

    except ImportError:
        logger.info("  [Playwright] Not installed, skipping.")
        return None
    except Exception as e:
        logger.info(f"  [Playwright] Error scraping {url[:60]}: {e}")
        return None


def scrape_article(url, fallback_content=None, force=False):
    """
    Main scraping entry point.
    The fallback order stays within the publisher's own surface area:
    direct HTML extraction, content variants, structured metadata, then
    caller-provided RSS/API descriptions.
    Returns a ScrapeResult with content and telemetry.
    """
    if is_domain_blocked(url):
        logger.info(f"  [Scraper] Domain blocked, skipping: {url[:60]}")
        return ScrapeResult(
            content=None,
            status=SCRAPE_STATUS_BLOCKED,
            method="blocklist",
            failure_reason="domain_blocked",
        )

    if should_skip(url):
        logger.info(f"  [Scraper] Skipping {url[:60]}")
        return ScrapeResult(
            content=None,
            status=SCRAPE_STATUS_SKIPPED,
            method="skiplist",
            failure_reason="unsupported_domain",
        )

    if not force:
        scope, deferral = get_retry_deferral(url)
        if deferral:
            logger.info(
                "  [Scraper] Deferring retry for %s (%s until %s)",
                url[:60],
                scope,
                deferral.get("defer_until"),
            )
            return ScrapeResult(
                content=None,
                status=SCRAPE_STATUS_FAILED,
                method="retry_cache",
                failure_reason=f"retry_deferred_{scope}:{deferral.get('failure_reason') or 'cooldown'}",
            )

    attempted_methods = []
    last_failure = None
    http_status = None
    initial_html = None

    def finalize_content(content, method, status=SCRAPE_STATUS_SUCCESS, http_status_override=None):
        if content:
            is_bad, reason = detect_bad_scrape(content)
            if is_bad:
                logger.warning(f"  [Scraper] {reason} — clearing content and blocking domain for {url[:60]}")
                add_to_blocklist(url, reason)
                return ScrapeResult(
                    content=None,
                    status=SCRAPE_STATUS_BLOCKED,
                    method=method,
                    failure_reason=reason,
                    http_status=http_status_override if http_status_override is not None else http_status,
                )
        result = ScrapeResult(
            content=content,
            status=status,
            method=method,
            http_status=http_status_override if http_status_override is not None else http_status,
        )
        update_retry_cache(url, result)
        return result

    def try_bs4(candidate_url, headers=None, method="bs4"):
        nonlocal last_failure, http_status, initial_html
        attempted_methods.append(method)
        try:
            response = _fetch_html(candidate_url, headers=headers)
            http_status = response.status_code
            if candidate_url == url and headers == HEADERS_DEFAULT:
                initial_html = response.text
            content = extract_with_readability(response.text, candidate_url)
            if content:
                return finalize_content(content, method)

            content = extract_structured_metadata(response.text, candidate_url)
            if content:
                return finalize_content(content, f"{method}_metadata", SCRAPE_STATUS_FALLBACK)

            content = extract_article_html_bs4(candidate_url, headers=headers)
            if content:
                return finalize_content(content, method)
        except requests.HTTPError as exc:
            http_status = exc.response.status_code if exc.response is not None else http_status
            last_failure = f"http_{http_status}" if http_status else "http_error"
            logger.info(f"  [Scraper] HTTP error for {candidate_url[:60]}: {last_failure}")
        except Exception as exc:
            last_failure = str(exc)
            logger.info(f"  [Scraper] Error for {candidate_url[:60]}: {exc}")
        return None

    if use_googlebot(url):
        logger.info(f"  [Scraper] Using Googlebot UA for {url[:60]}")
        result = try_bs4(url, headers=HEADERS_GOOGLEBOT, method="googlebot_bs4")
        if result and result.succeeded:
            return result

    result = try_bs4(url, method="bs4")
    if result and result.succeeded:
        return result

    if needs_playwright(url):
        logger.info(f"  [Scraper] Using Playwright for {url[:60]}")
        attempted_methods.append("playwright")
        content = extract_article_html_playwright(url)
        if content:
            return finalize_content(content, "playwright")

    if should_try_variant_urls(url=url, http_status=http_status, failure_reason=last_failure):
        for variant_url in build_variant_urls(url, html=initial_html):
            logger.info(f"  [Scraper] Trying variant {variant_url[:80]}")
            variant_result = try_bs4(variant_url, method="variant_bs4")
            if variant_result and variant_result.succeeded:
                return ScrapeResult(
                    content=variant_result.content,
                    status=SCRAPE_STATUS_FALLBACK,
                    method="variant_bs4",
                    http_status=variant_result.http_status,
                )

    if initial_html:
        attempted_methods.append("metadata")
        content = extract_structured_metadata(initial_html, url)
        if content:
            return finalize_content(content, "metadata", SCRAPE_STATUS_FALLBACK)

    if fallback_content:
        logger.info(f"  [Scraper] Falling back to feed/API description for {url[:60]}")
        content = sanitize_html(f"<div><p>{bleach.clean(_clean_text(fallback_content), strip=True)}</p></div>")
        if len(_clean_text(fallback_content)) >= 80:
            return finalize_content(content, "feed_description", SCRAPE_STATUS_FALLBACK)

    result = ScrapeResult(
        content=None,
        status=SCRAPE_STATUS_FAILED,
        method=" > ".join(attempted_methods) if attempted_methods else None,
        failure_reason=last_failure or "extraction_failed",
        http_status=http_status,
    )
    update_retry_cache(url, result)
    return result
