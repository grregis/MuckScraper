"""HTML allowlist shared by the scraper (when content is stored) and the
templates (when it is shown).

Scraped article HTML and LLM-written text both come from outside, so they
are filtered on the way in AND on the way out: the stored copy is cleaned by
news_fetcher/scraper.py, and templates clean it again at render time in case
anything was stored by another path (an older scrape, a fallback, a manual
edit). Formatting survives; anything that can run code does not.
"""
import re

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


_TAG_RE = re.compile(r"<[^>]+>")


def strip_title_tags(text):
    """Remove embedded markup from a title/headline (some outlets, e.g.
    National Review's culture pieces, put <i>/<em>/<font> tags straight in
    their RSS <title>). Titles are always rendered as plain text (story
    cards, "In Brief", <title>/OG tags), never through clean_html, so a
    literal tag leaks onto the page as text instead of rendering.

    Tags are deleted outright, not replaced with a space -- titles has them
    sitting flush against a word with no whitespace of their own
    ("<i>Re</i>fund"), so inserting a space produces "Re fund". Any
    whitespace the title actually needs survives around the tags in the
    source text; this only removes the tags.
    """
    if not text:
        return text
    text = _TAG_RE.sub("", text)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", text).strip()
