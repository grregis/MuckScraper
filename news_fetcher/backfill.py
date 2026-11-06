"""Backfill: summarise and analyse every story already in the database.

Normal runs only write summaries and analyses for new work (the edition's
stories, or every story that gained articles in 24 h with ANALYZE_ALL_STORIES).
Stories from before that stay without them. The backfill goes through the
whole database, newest stories first or oldest first, and per story:

  1. summarises each article that has text and no summary
  2. writes the deep analysis of each article with enough text (500+ chars)
  3. multi-source stories: writes the story summary and deep report when they
     are missing, or rewrites them when an article arrived after the last
     summary (the same rule as ANALYZE_ALL_STORIES); single-source stories
     take the article's summary as the story summary

Each result is saved as soon as it is written, and an old summary or report
is only replaced once its new version exists, so stopping at any point is safe
and the next start carries on from what is still missing.

It runs inside the scheduler process, which owns the run schedule: it starts
only when no run is in progress and the next one is more than
STOP_BEFORE_RUN_MINUTES away, and it stops by itself that long before the
next run (or when a run starts anyway). It does not start again afterwards.

Control is through two AppSettings, so the admin page (Settings > LLM
providers) and the command line work the same way:

    BACKFILL_CONTROL_KEY  {"command": "start"|"stop", "order": ..., ...}
    BACKFILL_STATUS_KEY   state, counts, current story, last log lines

Command line, from the repo folder on the Docker host:

    docker compose exec scheduler python -m news_fetcher.backfill start [--oldest] [--yes]
    docker compose exec scheduler python -m news_fetcher.backfill attach
    docker compose exec scheduler python -m news_fetcher.backfill status
    docker compose exec scheduler python -m news_fetcher.backfill stop
"""
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BACKFILL_CONTROL_KEY = "backfill_control_v1"
BACKFILL_STATUS_KEY = "backfill_status_v1"
FETCH_RUN_STATUS_KEY = "fetch_run_status_v1"
STOP_BEFORE_RUN_MINUTES = 10
LLM_WAIT_SECONDS = 600
LOG_LINES_KEPT = 40
RUN_STALE_AFTER = timedelta(hours=3)   # same cutoff as the admin restart guard
SCHEDULE_TIMEZONE = ZoneInfo("America/New_York")   # = scheduler.TIMEZONE
ORDERS = ("newest", "oldest")
CONTENT_FLOOR = 500        # = summarizer.ARTICLE_DEEP_ANALYSIS_MIN_CHARS (on raw text here)
READABLE_MIN_CHARS = 200   # summarize_story / generate_deep_report skip below this

# Providers whose calls cost nothing per call on a typical install. Anything
# else (Gemini, Groq, an OpenAI-compatible endpoint) needs an explicit
# acknowledgement, since a full backfill is several calls per story.
LOCAL_PROVIDERS = ("ollama",)

_thread_lock = threading.Lock()
_thread = None


# ────────────────────────────── pure helpers ──────────────────────────────

def next_run_after(now, hours):
    """The next scheduled run strictly after `now` (aware datetime), given
    the active schedule hours, or None when nothing is scheduled."""
    hours = sorted(set(hours))
    if not hours:
        return None
    for day in range(0, 2):
        base = (now + timedelta(days=day)).replace(minute=0, second=0, microsecond=0)
        for hour in hours:
            candidate = base.replace(hour=hour)
            if candidate > now:
                return candidate
    return None


def stop_time(upcoming):
    return upcoming - timedelta(minutes=STOP_BEFORE_RUN_MINUTES) if upcoming else None


def run_is_active(run_status, now_utc=None):
    """True if fetch_run_status_v1 says a run is going and isn't stale."""
    if not isinstance(run_status, dict) or run_status.get("status") != "running":
        return False
    now_utc = now_utc or datetime.utcnow()
    try:
        started = datetime.fromisoformat(run_status.get("started_at") or "")
    except ValueError:
        return True
    return now_utc - started < RUN_STALE_AFTER


def stop_reason(now, stop_at, upcoming, command, run_active):
    """Why the backfill must stop now, or None to carry on."""
    if command == "stop":
        return "stop requested"
    if run_active:
        return "a pipeline run started"
    if stop_at and now >= stop_at:
        return f"{STOP_BEFORE_RUN_MINUTES} minutes before the {fmt_time(upcoming)} run"
    return None


def fmt_time(dt):
    return dt.strftime("%-I:%M %p ET") if dt else ""


def normalise_order(order):
    return order if order in ORDERS else "newest"


def _is_local_host(url):
    """True for an endpoint on this machine or the local network."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    host = urlparse(url or "").hostname
    if not host:
        return False
    if host == "localhost" or "." not in host:   # localhost, a Compose service name
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        try:
            addr = ipaddress.ip_address(socket.gethostbyname(host))
        except (OSError, ValueError):
            return False
    return addr.is_private or addr.is_loopback


def needs_paid_ack(provider, host=None):
    """Whether starting needs an explicit "this may cost money" confirmation.
    Ollama never does; an OpenAI-compatible endpoint on the local network
    (llama.cpp, vLLM) doesn't either. Hosted providers do."""
    if provider in LOCAL_PROVIDERS:
        return False
    if provider == "openrouter":
        if host is None:
            from news_fetcher import llm_client
            host = getattr(llm_client, "OPENROUTER_HOST", "")
        return not _is_local_host(host)
    return True


# ──────────────────────────── settings storage ────────────────────────────

def _load(key):
    from aggregator import db
    from aggregator.models import AppSetting
    row = AppSetting.query.filter_by(key=key).first()
    value = row.value if row else None
    db.session.commit()   # end the read, so the next one sees new values
    try:
        return json.loads(value) if value else None
    except (TypeError, ValueError):
        return None


def _save(key, value):
    from aggregator import db
    from aggregator.models import AppSetting
    payload = json.dumps(value, default=str)
    row = AppSetting.query.filter_by(key=key).first()
    if row:
        row.value = payload
    else:
        db.session.add(AppSetting(key=key, value=payload))
    db.session.commit()


def get_status():
    return _load(BACKFILL_STATUS_KEY) or {"status": "idle"}


def get_control():
    return _load(BACKFILL_CONTROL_KEY) or {}


def is_running(status=None):
    status = status or get_status()
    return status.get("status") in ("starting", "running")


def request_start(order="newest", requested_by="cli", paid_ack=False):
    """Ask the scheduler to start a backfill. Returns (ok, message)."""
    from news_fetcher import llm_client
    if is_running():
        return False, "A backfill is already running."
    provider = llm_client.provider_for_tier(llm_client.TIER_QUALITY)
    if not llm_client.is_configured(llm_client.TIER_QUALITY):
        return False, "The main model is not configured."
    if needs_paid_ack(provider) and not paid_ack:
        return False, (f"Every call goes to {provider}. A backfill makes several calls per story; "
                       "confirm to go ahead (admin page checkbox, or --yes).")
    _save(BACKFILL_CONTROL_KEY, {
        "command": "start", "order": normalise_order(order),
        "requested_at": datetime.utcnow().isoformat(), "requested_by": requested_by,
    })
    return True, "Requested; the scheduler picks it up within 15 seconds."


def request_stop(requested_by="cli"):
    if not is_running():
        control = get_control()
        if control.get("command") == "start":   # not picked up yet: cancel it
            _save(BACKFILL_CONTROL_KEY, {"command": "stop", "requested_at": datetime.utcnow().isoformat(),
                                         "requested_by": requested_by})
            return True, "Cancelled the start request."
        return False, "No backfill is running."
    _save(BACKFILL_CONTROL_KEY, {"command": "stop", "requested_at": datetime.utcnow().isoformat(),
                                 "requested_by": requested_by})
    return True, "Stopping after the call in progress."


def mark_interrupted_at_startup():
    """Scheduler start: a backfill marked running died with the old process."""
    status = get_status()
    if is_running(status):
        status.update(status="stopped", stop_reason="the scheduler restarted",
                      finished_at=datetime.utcnow().isoformat())
        _save(BACKFILL_STATUS_KEY, status)
    control = get_control()
    if control.get("command") == "stop":
        _save(BACKFILL_CONTROL_KEY, {})


# ─────────────────────────────── the runner ───────────────────────────────

class _Stop(Exception):
    pass


class _Reporter:
    """Keeps the status row current: counts, current story, last log lines."""

    def __init__(self, order):
        self.lines = deque(maxlen=LOG_LINES_KEPT)
        self.started = time.monotonic()
        self.status = {
            "status": "starting", "order": order,
            "started_at": datetime.utcnow().isoformat(), "finished_at": None,
            "stop_reason": None, "stop_at": None, "total": None, "done": 0,
            "current": None, "provider": None, "model": None,
            "counts": {"article summaries": 0, "article analyses": 0, "story summaries": 0,
                       "deep reports": 0, "calls with no result": 0},
        }

    def log(self, line, save=True):
        logger.info("[Backfill] %s", line)
        self.lines.append(f"{datetime.now(SCHEDULE_TIMEZONE):%H:%M:%S} {line}")
        if save:
            self.save()

    def save(self, **fields):
        self.status.update(fields)
        self.status["log"] = list(self.lines)
        self.status["updated_at"] = datetime.utcnow().isoformat()
        self.status["minutes"] = round((time.monotonic() - self.started) / 60, 1)
        _save(BACKFILL_STATUS_KEY, self.status)


def _schedule_hours():
    from aggregator import db
    from aggregator.models import PipelineSchedule
    hours = [row.hour for row in PipelineSchedule.query.filter_by(is_active=True).all()]
    db.session.commit()
    return hours


def _wake_box(report):
    """Wake-on-LAN the local model box if one is configured (OLLAMA_MAC)."""
    mac = os.environ.get("OLLAMA_MAC", "").strip()
    if not mac:
        return
    try:
        import wakeonlan
        wakeonlan.send_magic_packet(mac)
    except Exception as e:   # a failed packet is not fatal: the wait below decides
        report.log(f"Wake-on-LAN failed: {e}")


def _wait_for_model(report, check):
    """Wait until the main model answers. Returns False on timeout."""
    from news_fetcher import llm_client
    deadline = time.monotonic() + LLM_WAIT_SECONDS
    report.log("Waiting for the main model to answer ...")
    while True:
        check()
        if llm_client.check_llm_status(llm_client.TIER_QUALITY) and llm_client.generate_text(
                "Reply with the single word OK.", timeout=90, tier=llm_client.TIER_QUALITY):
            return True
        if time.monotonic() > deadline:
            return False
        _wake_box(report)
        time.sleep(15)


def stories_needing_work_query():
    """(story_id, latest fetched_at) for every story with something missing
    or stale. Mirrors what the runner below fills in."""
    from sqlalchemy import and_, case, func, or_
    from aggregator import db
    from aggregator.models import Article, Story
    length = func.length(func.coalesce(Article.content, ""))
    n = func.count(Article.id)
    # A multi-article story summary or deep report needs at least one article
    # with readable text; without one the summariser skips the story, so
    # picking it would only cost a wasted visit on every start.
    readable = func.sum(case((length >= READABLE_MIN_CHARS, 1), else_=0)) > 0
    return (
        db.session.query(Story.id, func.max(Article.fetched_at).label("latest"))
        .join(Article, Article.story_id == Story.id)
        .group_by(Story.id)
        .having(or_(
            # single-source story without a summary, whose article has one or can get one
            and_(n == 1, func.coalesce(Story.summary, "") == "",
                 func.sum(case((or_(func.coalesce(Article.content, "") != "",
                                    func.coalesce(Article.summary, "") != ""), 1), else_=0)) > 0),
            # multi-source story: summary or report missing, or stale
            and_(n > 1, readable, or_(
                func.coalesce(Story.summary, "") == "",
                func.coalesce(Story.deep_report, "") == "",
                and_(Story.summary_generated_at.isnot(None),
                     func.max(Article.fetched_at) > Story.summary_generated_at))),
            # an article with text and no summary
            func.sum(case((and_(func.coalesce(Article.content, "") != "",
                                func.coalesce(Article.summary, "") == ""), 1), else_=0)) > 0,
            # an article long enough to analyse, without an analysis
            func.sum(case((and_(length >= CONTENT_FLOOR,
                                func.coalesce(Article.deep_analysis, "") == ""), 1), else_=0)) > 0,
        ))
    )


def _batches(order, size=500):
    """Stories needing work in the chosen order, a batch at a time, keyed on
    (latest article, story id) so work done meanwhile can't shift the walk."""
    from sqlalchemy import and_, func, or_
    from aggregator import db
    from aggregator.models import Article, Story
    base = stories_needing_work_query()
    latest = func.max(Article.fetched_at)
    cursor = None
    while True:
        q = base
        if cursor:
            if order == "oldest":
                q = q.having(or_(latest > cursor[0], and_(latest == cursor[0], Story.id > cursor[1])))
            else:
                q = q.having(or_(latest < cursor[0], and_(latest == cursor[0], Story.id < cursor[1])))
        if order == "oldest":
            q = q.order_by(latest.asc(), Story.id.asc())
        else:
            q = q.order_by(latest.desc(), Story.id.desc())
        batch = q.limit(size).all()
        db.session.commit()
        if not batch:
            return
        for story_id, story_latest in batch:
            cursor = (story_latest, story_id)
            yield story_id, story_latest


def _short(text, n=70):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def run_backfill(order="newest"):
    """Run one backfill to completion or until it must stop. Needs an app
    context. Writes progress to BACKFILL_STATUS_KEY throughout."""
    from aggregator import db
    from aggregator.models import Story
    from news_fetcher import llm_client
    from news_fetcher import summarizer as sm

    order = normalise_order(order)
    report = _Reporter(order)
    report.save()
    upcoming = next_run_after(datetime.now(SCHEDULE_TIMEZONE), _schedule_hours())
    stop_at = stop_time(upcoming)
    report.status["stop_at"] = stop_at.isoformat() if stop_at else None

    def check():
        reason = stop_reason(datetime.now(SCHEDULE_TIMEZONE), stop_at, upcoming,
                             get_control().get("command"), run_is_active(_load(FETCH_RUN_STATUS_KEY)))
        if reason:
            raise _Stop(reason)

    counts = report.status["counts"]

    def call(label, fn, *args):
        check()
        t = time.monotonic()
        try:
            result = fn(*args)
        except Exception as e:   # one bad call must not end the backfill
            result = None
            label += f" error: {e}"
        took = time.monotonic() - t
        if not result:
            counts["calls with no result"] += 1
        report.log(f"  {label} ... {'done' if result else 'no result, skipped'} ({took:.1f}s)")
        return result

    final, reason = "finished", None
    try:
        check()
        _wake_box(report)
        if not _wait_for_model(report, check):
            raise _Stop(f"the main model did not answer within {LLM_WAIT_SECONDS // 60} minutes")
        provider = llm_client.provider_for_tier(llm_client.TIER_QUALITY)
        model = llm_client.model_for_tier(llm_client.TIER_QUALITY)
        total = stories_needing_work_query().count()
        db.session.commit()
        report.save(status="running", provider=provider, model=model, total=total)
        report.log(f"{total:,} stories need work, {order} first. Main model: {model} via {provider}.")
        report.log(f"Stops by itself at {fmt_time(stop_at)} (next run {fmt_time(upcoming)})."
                   if upcoming else "No scheduled runs; stops when asked.")

        for story_id, latest in _batches(order):
            check()
            story = db.session.get(Story, story_id)
            if story is None:
                continue
            articles = sorted(story.articles, key=lambda a: a.fetched_at or datetime.min, reverse=True)
            n = len(articles)
            report.status["done"] += 1
            report.status["current"] = {"id": story.id, "headline": _short(story.headline or story.title),
                                        "latest": f"{latest:%Y-%m-%d}" if latest else None, "articles": n}
            report.log(f"[{report.status['done']:,}/{total:,}] Story {story.id} · {n} article"
                       f"{'s' if n != 1 else ''} · latest {f'{latest:%Y-%m-%d}' if latest else '?'} · "
                       f"{_short(story.headline or story.title)}")

            for a in articles:
                if a.content and not a.summary:
                    text = call(f"article {a.id} summary", sm.summarize_article, a)
                    if text:
                        a.summary = text
                        counts["article summaries"] += 1
                        db.session.commit()
            for a in articles:
                if not a.deep_analysis and sm.article_has_analysable_content(a):
                    text = call(f"article {a.id} analysis", sm.generate_article_deep_analysis, a)
                    if text:
                        a.deep_analysis = text
                        counts["article analyses"] += 1
                        db.session.commit()

            readable = any(len(sm.strip_html(a.content or "").strip()) >= READABLE_MIN_CHARS
                           for a in articles)
            if n >= 2 and not readable:
                report.log("  no article with readable text: no story summary or report possible", save=False)
            elif n >= 2:
                stale = bool(story.summary_generated_at) and any(
                    a.fetched_at and a.fetched_at > story.summary_generated_at for a in articles)
                redo_summary = not story.summary or stale
                redo_report = not story.deep_report or stale
                all_written = True
                if redo_summary:
                    text = call("story summary", sm.summarize_story, story)
                    if text:
                        story.summary = text
                        counts["story summaries"] += 1
                        db.session.commit()
                    else:
                        all_written = False
                if redo_report:
                    text = call("deep report", sm.generate_deep_report, story)
                    if text:
                        story.deep_report = text
                        counts["deep reports"] += 1
                        db.session.commit()
                    else:
                        all_written = False
                # Mark the story current only when both are, so a failed call
                # leaves it stale and it is retried next time.
                if (redo_summary or redo_report) and all_written:
                    story.summary_generated_at = datetime.utcnow()
                    db.session.commit()
            else:
                a = articles[0]
                if not story.summary and a.summary:
                    story.summary = a.summary
                    story.summary_generated_at = datetime.utcnow()
                    counts["story summaries"] += 1
                    db.session.commit()
                    report.log("  story summary = article summary", save=False)
            db.session.expunge_all()
        report.log("Everything is summarised.", save=False)
    except _Stop as why:
        db.session.rollback()
        final, reason = "stopped", str(why)
        report.log(f"Stopped: {why}.", save=False)
    except Exception as e:
        db.session.rollback()
        final, reason = "failed", str(e)
        logger.exception("[Backfill] failed")
        report.log(f"Failed: {e}", save=False)
    finally:
        if get_control().get("command") == "stop":
            _save(BACKFILL_CONTROL_KEY, {})
        report.save(status=final, stop_reason=reason, current=None,
                    finished_at=datetime.utcnow().isoformat())
    return report.status


def poll(app):
    """Scheduler job, every 15 s: start a requested backfill in its own thread."""
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        with app.app_context():
            control = get_control()
            if control.get("command") != "start":
                return
            _save(BACKFILL_CONTROL_KEY, {})   # consumed: one start per request
            order = normalise_order(control.get("order"))

        def target():
            with app.app_context():
                run_backfill(order)

        _thread = threading.Thread(target=target, name="backfill", daemon=True)
        _thread.start()


# ─────────────────────────────── command line ───────────────────────────────

def _print_status(status):
    state = status.get("status", "idle")
    line = f"Backfill: {state}"
    if status.get("order"):
        line += f", {status['order']} first"
    if status.get("total") is not None:
        line += f", {status.get('done', 0):,} of {status['total']:,} stories"
    if status.get("minutes") is not None:
        line += f", {status['minutes']:.0f} min"
    print(line)
    if status.get("stop_reason"):
        print(f"Reason: {status['stop_reason']}")
    counts = status.get("counts") or {}
    if counts:
        print(", ".join(f"{v:,} {k}" for k, v in counts.items()))


def _attach():
    """Follow the log. q = stop after the current call, d (or Ctrl+C) = detach."""
    interactive = sys.stdin.isatty()
    old = None
    if interactive:
        import termios
        import tty
        old = termios.tcgetattr(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())
        print("[q = stop after the current call · d = detach, it keeps running]", flush=True)
    seen = None
    try:
        idle_polls = 0
        while True:
            status = get_status()
            lines = status.get("log") or []
            start = 0
            if seen is not None and seen in lines:
                start = lines.index(seen) + 1
            elif seen is None:
                start = max(0, len(lines) - 25)
            for line in lines[start:]:
                print(line, flush=True)
            if lines:
                seen = lines[-1]
            if is_running(status) or get_control().get("command") == "start":
                idle_polls = 0
            else:
                idle_polls += 1
                if idle_polls > 1:
                    print()
                    _print_status(status)
                    return
            if interactive:
                import select
                ready, _, _ = select.select([sys.stdin], [], [], 2)
                key = sys.stdin.read(1).lower() if ready else ""
                if key == "q":
                    request_stop("cli")
                    print("  q: stopping after the call in progress ... (d to stop watching)", flush=True)
                elif key == "d":
                    raise KeyboardInterrupt
            else:
                time.sleep(2)
    except KeyboardInterrupt:
        print("\nDetached; the backfill keeps running in the scheduler.\n"
              "  python -m news_fetcher.backfill attach   watch it again (q stops it, d detaches)\n"
              "  python -m news_fetcher.backfill status   where it is\n"
              "  python -m news_fetcher.backfill stop     stop it after the call in progress", flush=True)
    finally:
        if old is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(prog="python -m news_fetcher.backfill",
                                     description="Summarise and analyse every story in the database.")
    parser.add_argument("command", choices=["start", "stop", "status", "attach"])
    parser.add_argument("--oldest", action="store_true", help="oldest stories first (default: newest first)")
    parser.add_argument("--yes", action="store_true", help="confirm when the main model is not Ollama")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    from aggregator import create_app
    app = create_app()
    with app.app_context():
        if args.command == "start":
            ok, message = request_start("oldest" if args.oldest else "newest", "cli", paid_ack=args.yes)
            print(message)
            if not ok:
                return 1
            _attach()
        elif args.command == "stop":
            ok, message = request_stop("cli")
            print(message)
        elif args.command == "status":
            status = get_status()
            _print_status(status)
            for line in (status.get("log") or [])[-8:]:
                print(line)
        else:
            _attach()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
