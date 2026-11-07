"""Backfill of summaries and analyses (news_fetcher/backfill.py; Settings >
LLM providers, and python -m news_fetcher.backfill).

Covers the stop rules, the newest/oldest order, the start/stop requests
through AppSettings, the paid-provider acknowledgement and the admin routes.
"""
import itertools
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from news_fetcher import backfill as bf

ET = bf.SCHEDULE_TIMEZONE


@pytest.fixture()
def app():
    from aggregator import create_app, db
    with patch.dict("os.environ", SECRET_KEY="backfill-fixture", DATABASE_URL="sqlite://"):
        app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


# ── pure helpers ──

def test_next_run_after_same_day_and_wraps_to_tomorrow():
    now = datetime(2026, 11, 6, 8, 30, tzinfo=ET)
    assert bf.next_run_after(now, [7, 12, 17, 22]) == datetime(2026, 11, 6, 12, 0, tzinfo=ET)
    late = datetime(2026, 11, 6, 22, 0, tzinfo=ET)          # exactly on a run: the next one
    assert bf.next_run_after(late, [7, 12, 17, 22]) == datetime(2026, 11, 7, 7, 0, tzinfo=ET)
    assert bf.next_run_after(now, []) is None


def test_stop_reason_order_of_checks():
    upcoming = datetime(2026, 11, 6, 12, 0, tzinfo=ET)
    stop_at = bf.stop_time(upcoming)
    assert stop_at == datetime(2026, 11, 6, 11, 50, tzinfo=ET)
    before = datetime(2026, 11, 6, 11, 49, tzinfo=ET)
    assert bf.stop_reason(before, stop_at, upcoming, None, False) is None
    assert bf.stop_reason(before, stop_at, upcoming, "stop", False) == "stop requested"
    assert bf.stop_reason(before, stop_at, upcoming, None, True) == "a pipeline run started"
    assert "before the 12:00 PM ET run" in bf.stop_reason(stop_at, stop_at, upcoming, None, False)
    assert bf.stop_reason(before, None, None, None, False) is None   # nothing scheduled


def test_run_is_active_ignores_stale_and_idle():
    now = datetime(2026, 11, 6, 12, 0)
    assert bf.run_is_active({"status": "running", "started_at": "2026-11-06T11:00:00"}, now)
    assert not bf.run_is_active({"status": "running", "started_at": "2026-11-06T08:00:00"}, now)
    assert not bf.run_is_active({"status": "idle"}, now)
    assert not bf.run_is_active(None, now)


def test_order_and_paid_ack():
    assert bf.normalise_order("oldest") == "oldest"
    assert bf.normalise_order("sideways") == "newest"
    assert not bf.needs_paid_ack("ollama")
    assert bf.needs_paid_ack("gemini") and bf.needs_paid_ack("groq")
    assert not bf.needs_paid_ack("openrouter", "http://192.168.1.50:8080/v1")     # llama.cpp on the LAN
    assert not bf.needs_paid_ack("openrouter", "http://localhost:8000/v1")
    assert not bf.needs_paid_ack("openrouter", "http://vllm:8000/v1")            # a Compose service
    assert bf.needs_paid_ack("openrouter", "https://8.8.8.8/api/v1")             # a public address
    assert bf.needs_paid_ack("openrouter", "")


# ── requests ──

def test_start_needs_ack_on_paid_provider(app):
    with patch("news_fetcher.llm_client.provider_for_tier", return_value="gemini"), \
         patch("news_fetcher.llm_client.is_configured", return_value=True):
        ok, message = bf.request_start("oldest", "test")
        assert not ok and "gemini" in message
        assert bf.get_control() == {}
        ok, _ = bf.request_start("oldest", "test", paid_ack=True)
        assert ok and bf.get_control()["command"] == "start" and bf.get_control()["order"] == "oldest"


def test_start_refused_while_running_and_stop_cancels_pending(app):
    with patch("news_fetcher.llm_client.provider_for_tier", return_value="ollama"), \
         patch("news_fetcher.llm_client.is_configured", return_value=True):
        ok, _ = bf.request_start("newest", "test")
        assert ok
        ok, message = bf.request_stop("test")              # not picked up yet: cancelled
        assert ok and "Cancelled" in message and bf.get_control()["command"] == "stop"
        bf._save(bf.BACKFILL_STATUS_KEY, {"status": "running"})
        ok, message = bf.request_start("newest", "test")
        assert not ok and "already running" in message


def test_scheduler_restart_marks_running_backfill_stopped(app):
    bf._save(bf.BACKFILL_STATUS_KEY, {"status": "running"})
    bf._save(bf.BACKFILL_CONTROL_KEY, {"command": "stop"})
    bf.mark_interrupted_at_startup()
    status = bf.get_status()
    assert status["status"] == "stopped" and status["stop_reason"] == "the scheduler restarted"
    assert bf.get_control() == {}


def test_poll_consumes_the_request_once(app):
    bf._save(bf.BACKFILL_CONTROL_KEY, {"command": "start", "order": "oldest"})
    calls = []
    with patch.object(bf, "run_backfill", side_effect=lambda order: calls.append(order)):
        bf.poll(app)
        bf._thread.join(5)
        bf.poll(app)                                         # nothing left to start
        if bf._thread:
            bf._thread.join(5)
    assert calls == ["oldest"]
    assert bf.get_control() == {}


# ── the runner ──

_urls = itertools.count()


def _story(day, n_articles=2, summarised=False, content="x" * 600):
    from aggregator import db
    from aggregator.models import Article, Story
    story = Story(title=f"Story {day}", summary="Done." if summarised else None,
                  deep_report="Done." if summarised else None,
                  summary_generated_at=datetime(2026, 11, 1, day, 59) if summarised else None)
    db.session.add(story)
    for i in range(n_articles):
        db.session.add(Article(title=f"A{day}-{i}", url=f"https://example.test/{next(_urls)}", source="Ex",
                               story=story, content=content, fetched_at=datetime(2026, 11, 1, day, i),
                               summary="S." if summarised else None, deep_analysis="D." if summarised else None))
    db.session.commit()
    return story.id


def _run(order, stop_after=None):
    """run_backfill with the summariser mocked; returns story ids in the order touched."""
    from news_fetcher import summarizer as sm
    touched = []

    def summarize_story(story):
        touched.append(story.id)
        if stop_after and len(touched) >= stop_after:
            bf._save(bf.BACKFILL_CONTROL_KEY, {"command": "stop"})
        return "A story summary."

    with patch.object(bf, "_wait_for_model", return_value=True), \
         patch.object(bf, "_schedule_hours", return_value=[]), \
         patch.object(sm, "summarize_article", return_value="An article summary."), \
         patch.object(sm, "generate_article_deep_analysis", return_value="An analysis."), \
         patch.object(sm, "summarize_story", side_effect=summarize_story), \
         patch.object(sm, "generate_deep_report", return_value="A report."), \
         patch("news_fetcher.llm_client.provider_for_tier", return_value="ollama"), \
         patch("news_fetcher.llm_client.model_for_tier", return_value="m"):
        status = bf.run_backfill(order)
    return touched, status


@pytest.mark.parametrize("order", ["newest", "oldest"])
def test_order_and_everything_filled(app, order):
    from aggregator.models import Article, Story
    ids = [_story(day) for day in (1, 3, 2)]
    done = _story(4, summarised=True)                         # nothing missing: skipped
    touched, status = _run(order)
    by_day = [ids[0], ids[2], ids[1]]                         # days 1, 2, 3
    assert touched == (by_day if order == "oldest" else list(reversed(by_day)))
    assert done not in touched
    assert status["status"] == "finished" and status["total"] == 3 and status["done"] == 3
    assert status["counts"]["article summaries"] == 6 and status["counts"]["deep reports"] == 3
    assert all(s.summary and s.deep_report and s.summary_generated_at for s in Story.query.all())
    assert all(a.summary and a.deep_analysis for a in Article.query.all())
    assert status["log"] and status["log"][-1].endswith("Everything is summarised.")


def test_stop_request_ends_after_the_call_in_progress(app):
    for day in (1, 2, 3):
        _story(day)
    touched, status = _run("newest", stop_after=1)
    assert len(touched) == 1
    assert status["status"] == "stopped" and status["stop_reason"] == "stop requested"
    assert bf.get_control() == {}                             # the stop is consumed


def test_single_article_story_takes_the_article_summary(app):
    from aggregator.models import Story
    sid = _story(1, n_articles=1)
    touched, status = _run("newest")
    assert touched == []                                      # no story-summary call
    assert Story.query.get(sid).summary == "An article summary."


def test_story_without_readable_text_is_not_picked(app):
    """Paywalled stories: nothing to summarise, so no visit and no empty calls."""
    from aggregator import db
    from aggregator.models import Story
    blocked = _story(1, content="")
    short = _story(2, content="Too short to read.")
    good = _story(3)
    ids = {sid for sid, _ in bf.stories_needing_work_query().all()}
    db.session.commit()
    assert ids == {short, good}          # short: its articles still get summaries
    touched, status = _run("newest")
    assert touched == [good] and status["counts"]["calls with no result"] == 0
    assert status["counts"]["article summaries"] == 4
    assert Story.query.get(blocked).summary is None
    assert Story.query.get(short).summary is None


def test_refuses_to_start_during_a_run(app):
    bf._save(bf.FETCH_RUN_STATUS_KEY, {"status": "running", "started_at": datetime.utcnow().isoformat()})
    _story(1)
    touched, status = _run("newest")
    assert touched == [] and status["status"] == "stopped"
    assert status["stop_reason"] == "a pipeline run started"


# ── admin routes ──
# werkzeug's Client, not app.test_client(): the latter fails in this image's
# Flask/Werkzeug pairing (CLAUDE.md). Signed in with a minted session cookie.

def _client(app, role=None):
    from flask.sessions import SecureCookieSessionInterface
    from werkzeug.test import Client
    from werkzeug.wrappers import Response
    from aggregator import db
    from aggregator.models import User
    client = Client(app, Response)
    if role:
        user = User(username=role, email=f"{role}@example.test", role=role)
        user.set_password("x" * 12)
        db.session.add(user)
        db.session.commit()
        cookie = SecureCookieSessionInterface().get_signing_serializer(app).dumps(
            {"_user_id": str(user.id), "_fresh": True})
        client.set_cookie("session", cookie)
    return client


def _get(client):
    return client.get("/admin/settings/llm/backfill", base_url="http://localhost")


def _post(client, body):
    return client.post("/admin/settings/llm/backfill", json=body, base_url="http://localhost")


def test_admin_routes_start_stop_and_status(app):
    import json as _json
    client = _client(app, "admin")
    with patch("news_fetcher.llm_client.provider_for_tier", return_value="ollama"), \
         patch("news_fetcher.llm_client.is_configured", return_value=True):
        assert _json.loads(_post(client, {"action": "start", "order": "oldest"}).data)["ok"]
        status = _json.loads(_get(client).data)
        assert status["pending_start"] and status["status"] == "idle"
        assert bf.get_control()["order"] == "oldest"
        assert _json.loads(_post(client, {"action": "stop"}).data)["ok"]
    assert not _json.loads(_get(client).data)["pending_start"]


@pytest.mark.parametrize("role", [None, "reader", "scraper"])
def test_admin_routes_need_admin(app, role):
    client = _client(app, role)
    assert _get(client).status_code in (302, 403)
    assert _post(client, {"action": "start"}).status_code in (302, 403)
    assert bf.get_control() == {}
