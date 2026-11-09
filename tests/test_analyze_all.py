"""Opt-in "analyse every story" (ANALYZE_ALL_STORIES / Settings > LLM providers).

Off by default; the Settings switch overrides .env until it is reset; when on,
every story with recent articles is filled, and a story's summary and deep
report are rewritten after a single new article (editions wait for three).
"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import url_for

from news_fetcher.fetch_and_store_articles import edition_content as ec


@pytest.fixture()
def app():
    from aggregator import create_app, db
    with patch.dict("os.environ", SECRET_KEY="analyze-all-fixture", DATABASE_URL="sqlite://"):
        app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _set(value):
    from aggregator import db
    from aggregator.models import AppSetting
    AppSetting.query.filter_by(key=ec.ANALYZE_ALL_STORIES_SETTING_KEY).delete()
    if value is not None:
        db.session.add(AppSetting(key=ec.ANALYZE_ALL_STORIES_SETTING_KEY, value=value))
    db.session.commit()


@pytest.mark.parametrize("env,saved,expected", [
    (None, None, (False, "env")),          # default: off
    ("true", None, (True, "env")),
    ("yes", None, (True, "env")),
    ("false", None, (False, "env")),
    ("true", json.dumps(False), (False, "admin")),   # the switch wins over .env
    ("", json.dumps(True), (True, "admin")),
    ("true", "not json", (True, "env")),   # a broken row falls back to .env
])
def test_setting_precedence(app, env, saved, expected):
    environ = {} if env is None else {"ANALYZE_ALL_STORIES": env}
    _set(saved)
    with patch.dict("os.environ", environ, clear=False):
        if env is None:
            import os
            os.environ.pop("ANALYZE_ALL_STORIES", None)
        assert ec.analyze_all_stories_setting() == expected


import itertools
_url_ids = itertools.count()


def _story(n_articles, fetched_at, summary_at=None):
    from aggregator import db
    from aggregator.models import Article, Story
    story = Story(title="A story", summary="Old summary." if summary_at else None,
                  deep_report="Old report." if summary_at else None, summary_generated_at=summary_at)
    db.session.add(story)
    for i in range(n_articles):
        db.session.add(Article(title=f"Article {i}", url=f"https://example.test/{next(_url_ids)}",
                               source="Example", story=story, content="x" * 600,
                               summary="Article summary.", deep_analysis="Analysis.",
                               fetched_at=fetched_at[i] if isinstance(fetched_at, list) else fetched_at))
    db.session.commit()
    return story


def _metrics():
    return {k: 0 for k in ("stale_stories_reset", "stable_stories_skipped", "child_article_summaries_generated",
                           "child_article_analyses_generated", "story_summaries_generated", "deep_reports_generated")}


def test_one_new_article_rewrites_story_only_in_analyze_all_mode(app):
    then = datetime.utcnow() - timedelta(hours=3)
    story = _story(3, [then - timedelta(hours=1), then - timedelta(hours=1), datetime.utcnow()], summary_at=then)
    with patch("news_fetcher.summarizer.summarize_story", return_value="New summary.") as s, \
         patch("news_fetcher.summarizer.generate_deep_report", return_value="New report.") as d:
        m = _metrics()
        ec._fill_story_content(story, m)  # edition rule: 1 new < 3, keep
        assert story.summary == "Old summary." and not s.called and not d.called
        m = _metrics()
        ec._fill_story_content(story, m, analyse_articles=True, stale_threshold=1)
        assert story.summary == "New summary." and story.deep_report == "New report."
        assert m["stale_stories_reset"] == 1 and m["deep_reports_generated"] == 1


def test_failed_rewrite_keeps_the_old_text_and_retries(app):
    """2026-11-08: llama-server was down, the stale reset blanked 48 stories
    and the blanks were saved. Old text now stays until a rewrite lands."""
    then = datetime.utcnow() - timedelta(hours=3)
    story = _story(2, [then - timedelta(hours=1), datetime.utcnow()], summary_at=then)
    with patch("news_fetcher.summarizer.summarize_story", return_value=None), \
         patch("news_fetcher.summarizer.generate_deep_report", return_value=None):
        changed = ec._fill_story_content(story, _metrics(), analyse_articles=True, stale_threshold=1)
    from aggregator import db
    db.session.expire_all()
    assert story.summary == "Old summary." and story.deep_report == "Old report."
    assert story.summary_generated_at == then  # still stale: the next run retries
    assert changed is False


def test_partial_rewrite_stays_stale_so_the_report_is_retried(app):
    then = datetime.utcnow() - timedelta(hours=3)
    story = _story(2, [then - timedelta(hours=1), datetime.utcnow()], summary_at=then)
    with patch("news_fetcher.summarizer.summarize_story", return_value="New summary."), \
         patch("news_fetcher.summarizer.generate_deep_report", return_value=None):
        ec._fill_story_content(story, _metrics(), analyse_articles=True, stale_threshold=1)
    assert story.summary == "New summary." and story.deep_report == "Old report."
    assert story.summary_generated_at == then
    with patch("news_fetcher.summarizer.summarize_story", return_value="Newer summary."), \
         patch("news_fetcher.summarizer.generate_deep_report", return_value="New report."):
        ec._fill_story_content(story, _metrics(), analyse_articles=True, stale_threshold=1)
    assert story.deep_report == "New report." and story.summary_generated_at > then


def test_missing_article_analysis_is_written_in_analyze_all_mode(app):
    from aggregator import db
    story = _story(2, datetime.utcnow() - timedelta(hours=1), summary_at=datetime.utcnow())
    story.articles[0].deep_analysis = None
    db.session.commit()
    with patch.object(ec, "_auto_article_deep_analysis_enabled", return_value=False), \
         patch("news_fetcher.summarizer.generate_article_deep_analysis", return_value="Fresh analysis.") as g:
        ec._fill_story_content(story, _metrics())          # toggle off: edition path skips it
        assert not g.called
        ec._fill_story_content(story, _metrics(), analyse_articles=True, stale_threshold=1)
        assert story.articles[0].deep_analysis == "Fresh analysis."


def test_step_is_skipped_when_off(app):
    _set(None)
    with patch.dict("os.environ", {"ANALYZE_ALL_STORIES": "false"}), \
         patch.object(ec, "_fill_story_content") as fill:
        result = ec.analyze_all_recent_stories()
    assert result["status"] == "skipped" and result["reason"] == "off"
    fill.assert_not_called()


def test_step_fills_recent_stories_biggest_first_and_skips_old_ones(app):
    _set(json.dumps(True))
    now = datetime.utcnow()
    small = _story(1, now - timedelta(hours=1))
    big = _story(3, now - timedelta(hours=2))
    old = _story(2, now - timedelta(days=3))
    ids = (small.id, big.id, old.id)
    seen = []
    with patch.object(ec, "check_ollama_status", return_value=True), \
         patch.object(ec, "_fill_story_content",
                      side_effect=lambda story, metrics, **kw: seen.append((story.id, kw)) or False):
        result = ec.analyze_all_recent_stories()
    assert [sid for sid, _ in seen] == [ids[1], ids[0]]
    assert all(kw == {"analyse_articles": True, "stale_threshold": 1} for _, kw in seen)
    assert result["candidates"] == 2 and result["status"] == "ok"


def test_step_stops_at_the_time_limit(app):
    _set(json.dumps(True))
    for _ in range(3):
        _story(1, datetime.utcnow())
    clock = iter([0, 0, 10, 10_000, 10_000, 10_000])
    with patch.object(ec, "check_ollama_status", return_value=True), \
         patch.object(ec, "_analyze_all_max_seconds", return_value=60), \
         patch.object(ec.time, "monotonic", side_effect=lambda: next(clock)), \
         patch.object(ec, "_fill_story_content", return_value=False):
        result = ec.analyze_all_recent_stories()
    assert result["status"] == "time_limit" and result["stories_seen"] == 2


def test_switch_saves_and_resets(app):
    admin = SimpleNamespace(is_authenticated=True, has_role=lambda minimum: True, username="admin")
    with app.test_request_context():
        path = url_for("admin.toggle_analyze_all_stories")
    with patch("aggregator.permissions.current_user", admin):
        client = app.test_client()
        assert client.post(path, data={"enabled": "true"}).status_code == 302
        assert ec.analyze_all_stories_setting() == (True, "admin")
        client.post(path, data={"reset": "true"})
        with patch.dict("os.environ", {"ANALYZE_ALL_STORIES": "false"}):
            assert ec.analyze_all_stories_setting() == (False, "env")


@pytest.mark.parametrize("role", ["reader", "scraper"])
def test_switch_is_admin_only(app, role):
    user = SimpleNamespace(is_authenticated=True, has_role=lambda minimum: minimum == role)
    with app.test_request_context():
        path = url_for("admin.toggle_analyze_all_stories")
    with patch("aggregator.permissions.current_user", user):
        assert app.test_client().post(path, data={"enabled": "true"}).status_code == 403
    assert ec.analyze_all_stories_setting()[1] == "env"
