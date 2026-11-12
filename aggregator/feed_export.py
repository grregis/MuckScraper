"""
Feed export plugin system for MuckScraper.

Enables article and headline list views (such as /headlines and /articles)
to export their filtered results as RSS 2.0 XML (and JSON Feed) without
interfering with topic, time range, search, or pagination filters.

Usage in routes:
    from aggregator.feed_export import render_feed_or_template

    return render_feed_or_template(
        "headlines_feed.html",
        stories=stories,
        feed_title=f"MuckScraper — {edition_label}",
        feed_description="Curated top stories and balanced coverage from MuckScraper",
        **template_context
    )
"""

from datetime import datetime, timezone
from email.utils import format_datetime
from typing import List, Optional
from urllib.parse import urlencode
import hmac
import logging
import os
import xml.etree.ElementTree as ET
import bleach
from flask import request, url_for, Response, jsonify, render_template

logger = logging.getLogger(__name__)

BIAS_LABELS = {
    1: "Left",
    2: "Lean Left",
    3: "Center",
    4: "Lean Right",
    5: "Right",
}


def is_feed_export_enabled() -> bool:
    """Return True if feed export is enabled in environment (default True)."""
    return os.environ.get("ENABLE_FEED_EXPORT", "true").strip().lower() in ("1", "true", "yes", "on")


def is_feed_token_authorized() -> bool:
    """
    Check if feed access is authorized via FEED_ACCESS_TOKEN.
    Requires FEED_ACCESS_TOKEN to be configured in the environment AND
    presented in the request (via '?token=...' query param or Authorization header).
    Returns False if FEED_ACCESS_TOKEN is not configured or token does not match.
    """
    token_configured = os.environ.get("FEED_ACCESS_TOKEN", "").strip()
    if not token_configured:
        return False

    # Check query param ?token=...
    req_token = (request.args.get("token") or "").strip()
    if req_token and hmac.compare_digest(req_token, token_configured):
        return True

    # Check Authorization header (Bearer <token> or Token <token>)
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header:
        parts = auth_header.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ("bearer", "token") and hmac.compare_digest(parts[1], token_configured):
            return True

    return False


def request_wants_feed() -> bool:
    """Check if the incoming request asks for an RSS or JSON feed."""
    fmt = (request.args.get("format") or "").strip().lower()
    if fmt in ("rss", "feed", "xml", "atom", "json"):
        return True

    path = request.path.lower()
    if path.endswith((".rss", "/feed.xml", "/rss.xml", ".json")):
        return True

    # If format was not in query or path, check Accept header
    raw_accept = request.headers.get("Accept", "").lower()
    # Standard browser Accept header includes text/html; wildcard */* is not an explicit feed request
    if "text/html" in raw_accept or not raw_accept or raw_accept == "*/*":
        return False

    if "application/rss+xml" in raw_accept or "application/atom+xml" in raw_accept:
        return True

    if "application/json" in raw_accept:
        return True

    return False


def get_requested_feed_format() -> str:
    """Return 'json' if JSON Feed requested, otherwise 'rss'."""
    fmt = (request.args.get("format") or "").strip().lower()
    path = request.path.lower()

    if fmt in ("rss", "feed", "xml", "atom") or path.endswith((".rss", "/feed.xml", "/rss.xml")):
        return "rss"

    if fmt == "json" or path.endswith(".json"):
        return "json"

    raw_accept = request.headers.get("Accept", "").lower()
    if "application/json" in raw_accept and "application/rss+xml" not in raw_accept:
        return "json"

    return "rss"


def build_feed_url(feed_format: str = "rss") -> str:
    """Build the RSS or JSON feed URL for the current request, preserving existing query filters."""
    query = dict(request.args)
    query["format"] = feed_format
    # Clean out web page pagination for feeds (feed readers expect the top/first window)
    query.pop("page", None)

    return f"{request.path}?{urlencode(query)}"


def format_rfc822(dt: Optional[datetime]) -> str:
    """Format a naive or aware datetime as an RFC 822 string for RSS."""
    if not dt:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def generate_rss_xml(
    stories: List,
    channel_title: str,
    channel_description: str,
    channel_link: str,
    feed_url: Optional[str] = None,
    language: str = "en-us",
) -> str:
    """Render a list of Story objects into valid RSS 2.0 XML."""
    rss = ET.Element("rss", version="2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = channel_title
    ET.SubElement(channel, "link").text = channel_link
    ET.SubElement(channel, "description").text = channel_description or channel_title
    ET.SubElement(channel, "language").text = language
    ET.SubElement(channel, "lastBuildDate").text = format_rfc822(datetime.now(timezone.utc))

    if feed_url:
        atom_link = ET.SubElement(channel, "{http://www.w3.org/2005/Atom}link")
        atom_link.set("href", feed_url)
        atom_link.set("rel", "self")
        atom_link.set("type", "application/rss+xml")

    for story in stories:
        item = ET.SubElement(channel, "item")

        # Title: AI-generated headline or fallback to title
        story_id = getattr(story, "id", None) or 0
        story_title = getattr(story, "headline", None) or getattr(story, "title", None) or f"Story #{story_id}"
        ET.SubElement(item, "title").text = story_title

        # Canonical link to story
        try:
            story_url = url_for("public.view_story", story_id=story_id, _external=True)
        except Exception:
            story_url = f"{channel_link.rstrip('/')}/story/{story_id}"

        ET.SubElement(item, "link").text = story_url

        guid = ET.SubElement(item, "guid")
        guid.text = story_url
        guid.set("isPermaLink", "true")

        # pubDate
        pub_date = getattr(story, "summary_generated_at", None) or getattr(story, "created_at", None)
        ET.SubElement(item, "pubDate").text = format_rfc822(pub_date)

        # Topic Categories
        if hasattr(story, "topics") and story.topics:
            for t in story.topics:
                if getattr(t, "name", None):
                    ET.SubElement(item, "category").text = t.name

        # Enriched description
        desc_parts = []
        story_headline = getattr(story, "headline", None)
        story_title_orig = getattr(story, "title", None)
        story_summary = getattr(story, "summary", None)
        if story_headline and story_title_orig and story_headline != story_title_orig:
            desc_parts.append(f"<p><em>{bleach.clean(story_title_orig, strip=True)}</em></p>")

        if story_summary:
            clean_sum = bleach.clean(
                story_summary,
                tags=["p", "br", "ul", "ol", "li", "strong", "em", "b", "i"],
                strip=True,
            )
            desc_parts.append(f"<div>{clean_sum}</div>")

        # Coverage balance breakdown
        left = getattr(story, "left_bias_count", None)
        center = getattr(story, "center_bias_count", None)
        right = getattr(story, "right_bias_count", None)
        if left is not None or center is not None or right is not None:
            total_cov = (left or 0) + (center or 0) + (right or 0)
            desc_parts.append(
                f"<p><strong>Coverage balance ({total_cov} sources):</strong> "
                f"{left or 0} Left, {center or 0} Center, {right or 0} Right</p>"
            )

        # Source outlets list
        if hasattr(story, "articles") and story.articles:
            desc_parts.append("<p><strong>Reporting sources:</strong></p><ul>")
            for art in story.articles[:8]:
                art_outlet = art.outlet.name if getattr(art, "outlet", None) else (art.source or "Source")
                art_score = getattr(art, "bias_score", None)
                if art_score is None and getattr(art, "outlet", None):
                    art_score = getattr(art.outlet, "bias_score", None)
                art_bias = BIAS_LABELS.get(int(round(art_score))) if art_score else "Unrated"
                art_title = bleach.clean(art.title or "Article", strip=True)
                art_link = art.url or "#"
                desc_parts.append(
                    f'<li><a href="{art_link}">{art_title}</a> — {art_outlet} ({art_bias})</li>'
                )
            desc_parts.append("</ul>")

        desc_parts.append(f'<p><a href="{story_url}">View full story and analysis on MuckScraper</a></p>')

        desc_elem = ET.SubElement(item, "description")
        desc_elem.text = "".join(desc_parts)

    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="utf-8").decode("utf-8")


def generate_json_feed(
    stories: List,
    channel_title: str,
    channel_description: str,
    channel_link: str,
    feed_url: Optional[str] = None,
) -> Response:
    """Render stories into standard JSON Feed 1.1 format."""
    items = []
    for story in stories:
        story_id = getattr(story, "id", None) or 0
        try:
            story_url = url_for("public.view_story", story_id=story_id, _external=True)
        except Exception:
            story_url = f"{channel_link.rstrip('/')}/story/{story_id}"

        pub_date = getattr(story, "summary_generated_at", None) or getattr(story, "created_at", None)
        tags = [t.name for t in getattr(story, "topics", []) if getattr(t, "name", None)]

        raw_summary = getattr(story, "summary", "") or ""
        clean_summary = bleach.clean(
            raw_summary,
            tags=["p", "br", "ul", "ol", "li", "strong", "em", "b", "i"],
            strip=True,
        ) if raw_summary else ""

        items.append({
            "id": story_url,
            "url": story_url,
            "title": getattr(story, "headline", None) or getattr(story, "title", None) or f"Story #{story_id}",
            "summary": bleach.clean(raw_summary, tags=[], strip=True) if raw_summary else "",
            "content_html": f"<div>{clean_summary}</div>" if clean_summary else "",
            "date_published": format_rfc822(pub_date),
            "tags": tags,
        })

    payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": channel_title,
        "home_page_url": channel_link,
        "feed_url": feed_url or channel_link,
        "description": channel_description,
        "items": items,
    }
    return jsonify(payload)


def render_feed_or_template(
    template_name: str,
    stories: List,
    feed_title: str,
    feed_description: Optional[str] = None,
    **template_kwargs,
) -> Response:
    """
    Main plugin wrapper for article list views.
    If the request asks for an RSS/JSON feed, returns the formatted feed.
    Otherwise, passes feed_url to template_kwargs and renders the normal HTML template.
    """
    if is_feed_export_enabled() and request_wants_feed():
        feed_format = get_requested_feed_format()
        channel_link = request.base_url
        feed_url = build_feed_url(feed_format=feed_format)

        if feed_format == "json":
            return generate_json_feed(
                stories=stories,
                channel_title=feed_title,
                channel_description=feed_description or feed_title,
                channel_link=channel_link,
                feed_url=feed_url,
            )

        rss_xml = generate_rss_xml(
            stories=stories,
            channel_title=feed_title,
            channel_description=feed_description or feed_title,
            channel_link=channel_link,
            feed_url=feed_url,
        )
        return Response(rss_xml, mimetype="application/rss+xml; charset=utf-8")

    # Standard HTML request: calculate feed_url for auto-discovery in <head>
    feed_url = build_feed_url(feed_format="rss") if is_feed_export_enabled() else None
    return render_template(
        template_name,
        stories=stories,
        feed_url=feed_url,
        feed_title=feed_title,
        **template_kwargs,
    )
