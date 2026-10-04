"""The time zone the aggregator shows times in.

Timestamps are stored as naive UTC. DISPLAY_TIMEZONE (an IANA name, default
America/New_York, the zone news_fetcher/scheduler.py runs the pipeline
schedule in) decides how they are shown: the server-rendered dates in tables,
the custom date ranges on the search page, and the hover times from
static/js/local_time.js all use it, so every page agrees.
"""
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

logger = logging.getLogger(__name__)

DEFAULT_DISPLAY_TIMEZONE = "America/New_York"


def configured_timezone_name() -> str:
    """DISPLAY_TIMEZONE from the environment, or the default if unset or unknown."""
    name = (os.environ.get("DISPLAY_TIMEZONE") or "").strip() or DEFAULT_DISPLAY_TIMEZONE
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown DISPLAY_TIMEZONE %r; using %s", name, DEFAULT_DISPLAY_TIMEZONE)
        name = DEFAULT_DISPLAY_TIMEZONE
    return name


def display_zone() -> ZoneInfo:
    return ZoneInfo(current_app.config.get("DISPLAY_TIMEZONE", DEFAULT_DISPLAY_TIMEZONE))


def to_display(value: datetime | None) -> datetime | None:
    """A stored (naive UTC) datetime as an aware datetime in the display zone."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(display_zone())


def local_datetime(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Template filter: a stored datetime formatted in the display zone."""
    local = to_display(value)
    return local.strftime(fmt) if local else ""



def local_day_start_utc(day: date) -> datetime:
    """Midnight at the start of `day` in the display zone, as naive UTC, for
    comparing against stored timestamps."""
    local_midnight = datetime.combine(day, time.min, tzinfo=display_zone())
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def local_day_end_utc(day: date) -> datetime:
    """Exclusive end of `day` in the display zone (the next local midnight), as naive UTC."""
    return local_day_start_utc(day + timedelta(days=1))
