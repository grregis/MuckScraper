"""Per-user feed defaults: what All / Grouped Stories open with, and where
the site's front page lands.

Set on the profile page, or with "Save as my default" in the feed's Filters
popover. A URL parameter always wins over a default, so links and the
popover's options still show exactly what they say; the defaults only fill
in parameters a request leaves out.

Stored as one JSON AppSetting per user (`feed_defaults_v1:<user id>`) rather
than columns on `users`, so it needs no migration: an install that never sets
a default has no rows and behaves exactly as before.
"""
import json
import logging

from aggregator import db
from aggregator.models import AppSetting

logger = logging.getLogger(__name__)

KEY_PREFIX = "feed_defaults_v1:"

START_PAGES = {
    "headlines": "Headlines",
    "all": "All Stories",
    "grouped": "Grouped Stories",
}
# The keys of TIME_RANGES in blueprints/admin/articles.py, in display order.
RANGES = {
    "24h": "24 hours",
    "7d": "7 days",
    "30d": "30 days",
    "all": "All time",
}
# "all" plus SCRAPE_STATUS_FILTERS from blueprints/admin/__init__.py.
SCRAPE_STATUSES = ("all", "success", "fallback", "blocked", "failed", "skipped", "pending")

# The behaviour before defaults existed: land on Headlines, the last 7 days,
# every scrape status, single-article stories shown on All Stories.
DEFAULTS = {
    "start_page": "headlines",
    "range": "7d",
    "scrape_status": "all",
    "hide_single": False,
}


def _key(user_id):
    return f"{KEY_PREFIX}{user_id}"


def clean(raw):
    """`raw` (a dict, e.g. a form or stored JSON) as a full, valid set of
    defaults. Unknown or bad values fall back to DEFAULTS one by one."""
    raw = raw or {}
    prefs = dict(DEFAULTS)
    if raw.get("start_page") in START_PAGES:
        prefs["start_page"] = raw["start_page"]
    if raw.get("range") in RANGES:
        prefs["range"] = raw["range"]
    if raw.get("scrape_status") in SCRAPE_STATUSES:
        prefs["scrape_status"] = raw["scrape_status"]
    hide_single = raw.get("hide_single")
    if isinstance(hide_single, str):
        hide_single = hide_single.lower() in ("1", "true", "on", "yes")
    if hide_single is not None:
        prefs["hide_single"] = bool(hide_single)
    return prefs


def load(user):
    """The user's defaults, or DEFAULTS for a signed-out visitor or a user
    who never saved any."""
    if user is None or not getattr(user, "is_authenticated", False):
        return dict(DEFAULTS)
    setting = AppSetting.query.filter_by(key=_key(user.id)).first()
    if not setting or not setting.value:
        return dict(DEFAULTS)
    try:
        return clean(json.loads(setting.value))
    except (TypeError, ValueError):
        logger.warning("Unreadable feed defaults for user %s; using the built-in ones", user.id)
        return dict(DEFAULTS)


def save(user, raw):
    """Clean `raw`, store it as the user's defaults and return what was stored."""
    prefs = clean(raw)
    setting = AppSetting.query.filter_by(key=_key(user.id)).first()
    if setting:
        setting.value = json.dumps(prefs)
    else:
        db.session.add(AppSetting(key=_key(user.id), value=json.dumps(prefs)))
    db.session.commit()
    return prefs


def forget(user_id):
    """Drop a deleted user's defaults (the caller commits)."""
    AppSetting.query.filter_by(key=_key(user_id)).delete()
