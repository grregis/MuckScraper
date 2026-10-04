"""HTML allowlist shared by the scraper (when content is stored) and the
templates (when it is shown).

Scraped article HTML and LLM-written text both come from outside, so they
are filtered on the way in AND on the way out: the stored copy is cleaned by
news_fetcher/scraper.py, and templates clean it again at render time in case
anything was stored by another path (an older scrape, a fallback, a manual
edit). Formatting survives; anything that can run code does not.
"""
import bleach
from markupsafe import Markup, escape

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

# Applies to href/src: blocks javascript:, data: and similar URLs.
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def sanitize_html(raw_html):
    """Keep formatting tags; drop scripts, event handlers, iframes, forms and
    unsafe URLs. Disallowed tags are stripped, keeping their text."""
    return bleach.clean(
        raw_html or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )


def clean_html(raw_html):
    """Template-ready sanitized HTML."""
    return Markup(sanitize_html(raw_html))


def plain_text_br(text):
    """Escape ALL markup in plain text (LLM summaries) and keep line breaks.
    No stored summary or report contains HTML (checked 2026-10-04), so this
    changes nothing visible while making injected markup inert."""
    if not text:
        return Markup("")
    return Markup("<br>").join(escape(text).split("\n"))
