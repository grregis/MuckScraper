"""Return links ("Back to Feed") that take a reader back to the exact list
they came from -- section, filters, page and search terms.

Feed pages put their own path in a `back` query parameter on story and
article links; the story page passes it on to its article links, so
Feed -> Story -> Article -> Story -> Feed lands where the reader started.
The account menu does the same, so Profile / Users / Admin Tools can return
to the page the menu was opened from. Only same-site relative paths are
accepted (no scheme, no host, no "//"), so `back` can't send anyone off-site.
"""
from flask import request, url_for

# Pages a reader browses from. Anywhere else, the account menu carries the
# incoming `back` through instead of pointing back at itself.
FEED_ENDPOINTS = {
    "public.headlines_feed",
    "public.view_story",
    "public.view_article",
    "admin.list_articles",
    "admin.multi_article_stories",
    "admin.search_page",
    "admin.fetch_page",
}


def safe_back(value):
    """`value` if it is a same-site relative path, else None."""
    if not value or len(value) > 2000:
        return None
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    if any(ch in value for ch in "\r\n\t"):
        return None
    return value


def current_back():
    """This page's path and query string, for a `back` parameter."""
    return request.full_path.rstrip("?")


def incoming_back():
    """The validated `back` this page was opened with, or None."""
    return safe_back(request.args.get("back"))


def feed_back_url():
    """Where "Back to Feed" goes: the list the reader came from, else Headlines."""
    return incoming_back() or url_for("public.headlines_feed")


def menu_back():
    """`back` for account-menu links: this page if it is part of the feed,
    otherwise whatever this page was itself opened with."""
    if request.endpoint in FEED_ENDPOINTS:
        return current_back()
    return incoming_back()
