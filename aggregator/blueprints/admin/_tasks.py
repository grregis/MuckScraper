import json
import logging
import threading
from datetime import datetime
from flask import current_app, request, jsonify
from flask_login import login_required
from sqlalchemy import or_
from aggregator import db
from aggregator.models import AppSetting, Article, Story
from aggregator.search import reindex_all
from news_fetcher.llm_client import TIER_QUALITY

from . import admin, SEARCH_REINDEX_STATUS_KEY, _TASK_STATUS_KEY_PREFIXES, ORPHANED_TASK_STALE_AFTER, BULK_TASK_ACTIONS
from ._shared import _load_json_setting, _save_json_setting

logger = logging.getLogger(__name__)


def _try_claim_task_status(key, running_payload):
    """
    Atomically claim a task-status row for 'running', safe across multiple
    gunicorn worker processes (not just threads within one process).

    A plain "check status, then save 'running'" -- even guarded by an
    in-process threading.Lock -- only prevents a double-start from threads in
    the SAME process. With multiple worker processes, two requests landing on
    different workers would each pass the check independently (each process
    has its own lock and Python globals) and both start the same action.

    This does the check-and-set as one atomic SQL statement instead: the
    UPDATE (or INSERT, if the row doesn't exist yet) only takes effect if no
    other process already has the row marked 'running', and the database's
    row-level locking makes that atomic regardless of how many processes are
    asking at once. Returns True if this call won the claim, False if the
    action is already running (started by any process/thread).
    """
    payload_json = json.dumps(running_payload)
    result = db.session.execute(
        db.text("""
            INSERT INTO app_settings (key, value)
            VALUES (:key, :value)
            ON CONFLICT (key) DO UPDATE
                SET value = :value
                WHERE (app_settings.value::json ->> 'status') IS DISTINCT FROM 'running'
            RETURNING key
        """),
        {"key": key, "value": payload_json},
    )
    claimed = result.fetchone() is not None
    db.session.commit()
    return claimed


def reconcile_orphaned_task_statuses():
    """Mark any task-status stuck 'running' well past a plausible runtime as
    interrupted, so a task whose process died mid-run doesn't permanently
    block that action from being re-run (_start_bulk_task/etc. all refuse to
    start an action whose status is already 'running').

    Deliberately keyed on staleness (started_at age), not "found running at
    this process's boot": with multiple gunicorn worker processes (see
    GUNICORN_WORKERS), a 'running' status can be perfectly legitimate work
    happening in a *different* worker than the one currently starting up, so
    "I just (re)started, therefore every running status is dead" is not a
    safe assumption once there's more than one worker. Age-based staleness
    is correct regardless of how many workers or processes are involved, so
    this can run at any worker's startup (or be called periodically) without
    risk of killing a task that's actually still alive elsewhere.
    """
    cutoff = datetime.utcnow() - ORPHANED_TASK_STALE_AFTER
    rows = AppSetting.query.filter(
        or_(
            *[AppSetting.key.like(prefix + "%") for prefix in _TASK_STATUS_KEY_PREFIXES],
            AppSetting.key == SEARCH_REINDEX_STATUS_KEY,
        )
    ).all()
    reconciled = 0
    for row in rows:
        try:
            payload = json.loads(row.value) if row.value else None
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict) or payload.get("status") != "running":
            continue

        started_at = payload.get("started_at")
        try:
            started_dt = datetime.fromisoformat(started_at) if started_at else None
        except (TypeError, ValueError):
            started_dt = None
        if started_dt is None or started_dt > cutoff:
            # No parseable start time, or still within a plausible runtime --
            # leave it alone; it may genuinely still be running, possibly in
            # a different worker process than this one.
            continue

        payload["status"] = "error"
        payload["finished_at"] = datetime.utcnow().isoformat()
        payload["message"] = (
            f"No update in over {int(ORPHANED_TASK_STALE_AFTER.total_seconds() // 3600)}h; "
            "treated as interrupted (process likely died mid-task). Re-run to retry."
        )
        row.value = json.dumps(payload)
        reconciled += 1
    if reconciled:
        db.session.commit()
        logger.info("[Startup] Reconciled %d stale 'running' task status(es).", reconciled)
    return reconciled


def _search_reindex_status_payload():
    payload = _load_json_setting(SEARCH_REINDEX_STATUS_KEY)
    if payload:
        return payload
    return {
        "status": "idle",
        "started_at": None,
        "finished_at": None,
        "message": "Search index has not been rebuilt in this app session yet.",
        "story_documents": None,
        "article_documents": None,
    }


def _ai_task_status_key(task_type, resource_id):
    return f"ai_task_status_v1:{task_type}:{resource_id}"


def _ai_task_default_message(task_type):
    messages = {
        "story_summary": "Story summary has not been started in this app session yet.",
        "story_deep_report": "Story analysis has not been started in this app session yet.",
        "article_summary": "Article summary has not been started in this app session yet.",
    }
    return messages.get(task_type, "AI task has not been started in this app session yet.")


def _ai_task_status_payload(task_type, resource_id):
    payload = _load_json_setting(_ai_task_status_key(task_type, resource_id))
    if payload:
        return payload
    return {
        "status": "idle",
        "task_type": task_type,
        "resource_id": resource_id,
        "started_at": None,
        "finished_at": None,
        "message": _ai_task_default_message(task_type),
    }


def _save_ai_task_status(task_type, resource_id, payload):
    base_payload = {
        "task_type": task_type,
        "resource_id": resource_id,
    }
    base_payload.update(payload)
    _save_json_setting(_ai_task_status_key(task_type, resource_id), base_payload)


def _run_ai_task(app, task_type, resource_id):
    with app.app_context():
        started_at = _ai_task_status_payload(task_type, resource_id).get("started_at")

        try:
            from news_fetcher.summarizer import (
                summarize_story,
                summarize_article,
                generate_deep_report,
                check_ollama_status,
            )

            if not check_ollama_status(TIER_QUALITY):
                raise RuntimeError("The summarization LLM is offline.")

            if task_type == "story_summary":
                story = Story.query.get_or_404(resource_id)
                summary = summarize_story(story)
                if not summary:
                    raise RuntimeError("No story summary was generated.")
                story.summary = summary
                db.session.commit()
                message = "Story summary completed successfully."
            elif task_type == "story_deep_report":
                story = Story.query.get_or_404(resource_id)
                if len(story.articles) < 2:
                    raise RuntimeError("Story analysis requires at least two articles.")
                report = generate_deep_report(story)
                if not report:
                    raise RuntimeError("No story analysis was generated.")
                story.deep_report = report
                db.session.commit()
                message = "Story analysis completed successfully."
            elif task_type == "article_summary":
                article = Article.query.get_or_404(resource_id)
                summary = summarize_article(article)
                if not summary:
                    raise RuntimeError("No article summary was generated.")
                article.summary = summary
                db.session.commit()
                message = "Article summary completed successfully."
            else:
                raise RuntimeError(f"Unknown AI task type: {task_type}")

            _save_ai_task_status(
                task_type,
                resource_id,
                {
                    "status": "success",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": message,
                },
            )
        except Exception as e:
            db.session.rollback()
            logger.exception("Async AI task error type=%s resource_id=%s: %s", task_type, resource_id, e)
            _save_ai_task_status(
                task_type,
                resource_id,
                {
                    "status": "error",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": str(e),
                },
            )


def _run_search_reindex(app):
    with app.app_context():
        try:
            started_at = _search_reindex_status_payload().get("started_at")
            counts = reindex_all()
            _save_json_setting(
                SEARCH_REINDEX_STATUS_KEY,
                {
                    "status": "success",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": "Meilisearch reindex completed successfully.",
                    "story_documents": counts["story_documents"],
                    "article_documents": counts["article_documents"],
                },
            )
            logger.info(
                "[Search] Async reindex complete stories=%s articles=%s",
                counts["story_documents"],
                counts["article_documents"],
            )
        except Exception as e:
            logger.exception(f"Async search reindex error: {e}")
            started_at = _search_reindex_status_payload().get("started_at")
            _save_json_setting(
                SEARCH_REINDEX_STATUS_KEY,
                {
                    "status": "error",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": str(e),
                    "story_documents": None,
                    "article_documents": None,
                },
            )


def _bulk_task_status_key(action):
    return f"bulk_task_status_v1:{action}"


def _bulk_task_status_payload(action):
    payload = _load_json_setting(_bulk_task_status_key(action))
    if payload:
        return payload
    return {
        "status": "idle",
        "started_at": None,
        "finished_at": None,
        "message": f"{action} has not been run in this app session yet.",
    }


def _run_bulk_task(app, action, fn):
    with app.app_context():
        started_at = _bulk_task_status_payload(action).get("started_at")
        try:
            fn()
            _save_json_setting(
                _bulk_task_status_key(action),
                {
                    "status": "success",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": f"{action} completed successfully.",
                },
            )
            logger.info(f"[BulkTask] {action} completed")
        except Exception as e:
            db.session.rollback()
            logger.exception(f"[BulkTask] {action} failed: {e}")
            _save_json_setting(
                _bulk_task_status_key(action),
                {
                    "status": "error",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": str(e),
                },
            )


def _start_bulk_task(action, fn):
    """
    Run a long-running admin action (reclassify, resummarize, regroup, scrape
    backlog, etc.) in a background thread instead of inline in the request.
    These can take minutes on a full DB, which was blowing past gunicorn's
    worker timeout and killing the request mid-run (GitHub issue #3).

    Returns (started, status) — started is False (with the current running
    status) if this action is already in progress on any worker process.
    """
    started_at = datetime.utcnow().isoformat()
    claimed = _try_claim_task_status(
        _bulk_task_status_key(action),
        {
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "message": f"{action} is running.",
        },
    )
    if not claimed:
        return False, _bulk_task_status_payload(action)

    app = current_app._get_current_object()
    thread = threading.Thread(target=_run_bulk_task, args=(app, action, fn), daemon=True)
    thread.start()
    return True, _bulk_task_status_payload(action)


@admin.route("/bulk-task-status/<action>")
@login_required
def bulk_task_status(action):
    if action not in BULK_TASK_ACTIONS:
        return jsonify({"status": "error", "message": "Unknown action."}), 404
    return jsonify(_bulk_task_status_payload(action))


@admin.route("/ai-task/start", methods=["POST"])
@login_required
def start_ai_task():
    payload = request.get_json(silent=True) or request.form
    task_type = (payload.get("task_type") or "").strip()
    resource_id = payload.get("resource_id")

    try:
        resource_id = int(resource_id)
    except (TypeError, ValueError):
        return jsonify({
            "started": False,
            "message": "Invalid resource id.",
        }), 400

    if task_type not in {"story_summary", "story_deep_report", "article_summary"}:
        return jsonify({
            "started": False,
            "message": "Invalid AI task type.",
        }), 400

    started_at = datetime.utcnow().isoformat()
    claimed = _try_claim_task_status(
        _ai_task_status_key(task_type, resource_id),
        {
            "task_type": task_type,
            "resource_id": resource_id,
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "message": "AI task is running.",
        },
    )
    if not claimed:
        return jsonify({
            "started": False,
            "status": _ai_task_status_payload(task_type, resource_id),
        }), 409

    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_ai_task,
        args=(app, task_type, resource_id),
        daemon=True,
    )
    thread.start()

    return jsonify({
        "started": True,
        "status": _ai_task_status_payload(task_type, resource_id),
    }), 202


@admin.route("/ai-task-status/<task_type>/<int:resource_id>")
@login_required
def ai_task_status(task_type, resource_id):
    if task_type not in {"story_summary", "story_deep_report", "article_summary"}:
        return jsonify({
            "status": "error",
            "message": "Invalid AI task type.",
        }), 400
    return jsonify(_ai_task_status_payload(task_type, resource_id))


@admin.route("/reindex-search-status")
@login_required
def reindex_search_status():
    return jsonify(_search_reindex_status_payload())
