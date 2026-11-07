"""DISPLAY_TIMEZONE: stored naive-UTC times shown in the configured zone."""
from datetime import date, datetime

import pytest
from flask import Flask

from aggregator import display_time
from aggregator.display_time import (
    configured_timezone_name,
    local_datetime,
    local_day_end_utc,
    local_day_start_utc,
    to_display,
)


@pytest.fixture
def app_ctx():
    def make(zone):
        app = Flask(__name__)
        app.config["DISPLAY_TIMEZONE"] = zone
        return app.app_context()
    return make


def test_summer_time_is_utc_minus_four(app_ctx):
    with app_ctx("America/New_York"):
        assert local_datetime(datetime(2026, 9, 28, 16, 40)) == "2026-09-28 12:40"


def test_winter_time_is_utc_minus_five(app_ctx):
    with app_ctx("America/New_York"):
        assert local_datetime(datetime(2026, 1, 15, 16, 40)) == "2026-01-15 11:40"


def test_late_utc_evening_lands_on_the_previous_local_day(app_ctx):
    with app_ctx("America/New_York"):
        assert local_datetime(datetime(2026, 10, 4, 2, 6)) == "2026-10-03 22:06"


def test_other_zone(app_ctx):
    with app_ctx("Europe/London"):
        assert local_datetime(datetime(2026, 9, 28, 16, 40)) == "2026-09-28 17:40"


def test_none_renders_blank(app_ctx):
    with app_ctx("America/New_York"):
        assert local_datetime(None) == ""
        assert to_display(None) is None


def test_custom_range_covers_whole_local_days(app_ctx):
    with app_ctx("America/New_York"):
        assert local_day_start_utc(date(2026, 10, 3)) == datetime(2026, 10, 3, 4, 0)
        assert local_day_end_utc(date(2026, 10, 3)) == datetime(2026, 10, 4, 4, 0)


def test_day_containing_dst_change_is_25_hours(app_ctx):
    # Clocks go back on 2026-11-01 in New York.
    with app_ctx("America/New_York"):
        span = local_day_end_utc(date(2026, 11, 1)) - local_day_start_utc(date(2026, 11, 1))
        assert span.total_seconds() == 25 * 3600


def test_unset_or_unknown_zone_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("DISPLAY_TIMEZONE", raising=False)
    assert configured_timezone_name() == display_time.DEFAULT_DISPLAY_TIMEZONE
    monkeypatch.setenv("DISPLAY_TIMEZONE", "Not/AZone")
    assert configured_timezone_name() == display_time.DEFAULT_DISPLAY_TIMEZONE
    monkeypatch.setenv("DISPLAY_TIMEZONE", "Europe/London")
    assert configured_timezone_name() == "Europe/London"
