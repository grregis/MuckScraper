# news_fetcher/bylines.py
#
# Article bylines (Regis, 2026-10-24). The author comes from the feed or API
# entry at ingestion; when that has none, from the publisher's own page
# (JSON-LD, then author meta tags) if the scraper fetched it. Pure functions:
# nothing here fetches or writes.

import json
import re

from bs4 import BeautifulSoup

MAX_BYLINE_CHARS = 200

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_BY_PREFIX_RE = re.compile(r"^\s*(?:written\s+)?by[:\s]+", re.I)
_SPACE_RE = re.compile(r"\s+")
_ROLE_SUFFIX_RE = re.compile(
    r",\s*(?:[\w-]+\s+){0,3}(?:contributor|correspondent|reporter|writer|editor|columnist)s?$", re.I)

# Wire services and desks that follow a reporter's name ("Jane Doe, CNN",
# "Jane Doe | Reuters"), compared lowercase. The article's own outlet name is
# dropped the same way.
_TRAILING_OUTLETS = {
    "ap", "associated press", "the associated press", "reuters", "afp",
    "agence france-presse", "bloomberg", "cnn", "bbc", "bbc news", "upi",
}

# Bylines that name no person.
_NOT_A_PERSON = {
    "staff", "staff writer", "staff report", "staff reports", "admin", "editor",
    "editors", "the editors", "news desk", "newsroom", "web staff", "wire",
    "wire service", "wire reports", "unknown", "none", "null", "author",
}


def clean_byline(raw, source_name=None, outlet_names=()):
    """A display byline from whatever a feed, API or page gave, or None.

    "By Jane Doe" -> "Jane Doe"; "jdoe@x.com (Jane Doe)" -> "Jane Doe";
    "Jane Doe, CNN" -> "Jane Doe"; "Jane Doe and John Roe" stays as it is.
    URLs, e-mail addresses alone, outlet names and "Staff" give None.
    `outlet_names` (lowercase) are other outlets' names: a syndicated piece
    credited "Washington Post" names no person either.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = BeautifulSoup(raw, "html.parser").get_text(" ") if "<" in raw else raw
    text = _SPACE_RE.sub(" ", text).strip()

    # RSS 2.0 <author> is "email (Name)".
    match = re.fullmatch(r"\S+@\S+\s*\((.+)\)", text)
    if match:
        text = match.group(1).strip()
    text = _EMAIL_RE.sub("", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = _BY_PREFIX_RE.sub("", text)
    text = text.strip(" ,|;-–—")

    # The Guardian's "Jane Doe in Nairobi and agencies"; The Hill's
    # "Jane Doe, opinion contributor".
    text = re.sub(r"\s+and\s+agencies$", "", text, flags=re.I)
    text = re.sub(r"\s+in\s+[A-Z][\w.'-]*(?:\s+(?:(?:de|da|do|dos|das|del|la|le|el|al)\s+)?[A-Z][\w.'-]*)*(?=\s+and\s|$)", "", text)
    text = _ROLE_SUFFIX_RE.sub("", text)

    own_outlet = (source_name or "").strip().lower()
    while True:
        parts = re.split(r"\s*[,|]\s*|\s+[-–—]\s+", text)
        if len(parts) > 1 and parts[-1].strip().lower() in (_TRAILING_OUTLETS | {own_outlet}):
            text = text[: text.lower().rfind(parts[-1].strip().lower())].rstrip(" ,|-–—")
            continue
        break

    text = _SPACE_RE.sub(" ", text).strip(" ,|;-–—")
    lowered = text.lower()
    if (not text or lowered in _NOT_A_PERSON or lowered in _TRAILING_OUTLETS
            or lowered == own_outlet or lowered in outlet_names or "://" in text or lowered.startswith("www.")
            or not re.search(r"[A-Za-z]", text) or re.search(r"\d", text)
            or re.search(r"\bstaff(?:\s+(?:writers?|reports?))?$", lowered)):
        return None
    if len(text) > MAX_BYLINE_CHARS:
        return None
    return text


def feed_entry_author(entry):
    """The author of a feedparser entry (dc:creator, <author>, or authors[])."""
    author = entry.get("author")
    if author:
        return author
    names = [a.get("name") for a in (entry.get("authors") or []) if isinstance(a, dict) and a.get("name")]
    return " and ".join(names) if names else None


def _jsonld_author_names(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [value["name"]] if isinstance(value.get("name"), str) else []
    if isinstance(value, list):
        names = []
        for item in value:
            names.extend(_jsonld_author_names(item))
        return names
    return []


def jsonld_items(payload):
    stack = payload if isinstance(payload, list) else [payload]
    for item in stack:
        if not isinstance(item, dict):
            continue
        yield item
        graph = item.get("@graph")
        if isinstance(graph, list):
            yield from (g for g in graph if isinstance(g, dict))


def _join_names(names):
    names = [n for n in dict.fromkeys(n.strip() for n in names) if n]
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def author_from_html(html, source_name=None):
    """The byline a publisher's page declares: JSON-LD article author, then
    the author meta tags. None when the page names no person."""
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or script.get_text() or "")
        except Exception:
            continue
        for item in jsonld_items(payload):
            if "author" not in item:
                continue
            names = [clean_byline(n, source_name) for n in _jsonld_author_names(item["author"])]
            byline = _join_names([n for n in names if n])
            if byline:
                return byline

    for attrs in ({"name": "author"}, {"property": "article:author"},
                  {"name": "byl"}, {"name": "parsely-author"}):
        tag = soup.find("meta", attrs=attrs)
        byline = clean_byline(tag.get("content"), source_name) if tag else None
        if byline:
            return byline
    return None
