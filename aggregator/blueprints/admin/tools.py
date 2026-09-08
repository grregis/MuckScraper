import json
import logging
from datetime import datetime
import requests
from flask import render_template, request, redirect, url_for
from flask_login import login_required
from aggregator.models import AppSetting

from . import admin, DOCKER_RESTART_PROXY_URL, CONTAINER_SERVICE_NAMES, MANUAL_FETCH_STATUS_KEY, FETCH_RUN_STATUS_KEY, BULK_TASK_ACTIONS, ORPHANED_TASK_STALE_AFTER
from ._shared import _load_json_setting, _save_json_setting
from ._tasks import _bulk_task_status_payload, _search_reindex_status_payload
from .config_crud import pipeline_schedule_restart_needed

logger = logging.getLogger(__name__)


@admin.route("/tools")
@login_required
def tools_page():
    from news_fetcher.fetch_and_store_articles import (
        AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY,
    )
    auto_deep = bool(_load_json_setting(AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY))

    running_ops = _running_operations()
    fetch_ops = [op for op in running_ops if op["kind"] == "fetch"]
    other_ops = [op for op in running_ops if op["kind"] == "other"]

    return render_template(
        "admin_tools.html",
        auto_article_deep_analysis_enabled=auto_deep,
        containers=_list_containers(),
        container_service_order=CONTAINER_SERVICE_NAMES,
        pipeline_schedule_restart_needed=pipeline_schedule_restart_needed(),
        fetch_running=bool(fetch_ops),
        fetch_running_ops=fetch_ops,
        other_running_ops=other_ops,
        restart_blocked_reason=request.args.get("blocked_reason"),
        restart_blocked_target=request.args.get("blocked_target"),
    )


@admin.route("/toggle-auto-article-deep-analysis", methods=["POST"])
@login_required
def toggle_auto_article_deep_analysis():
    from news_fetcher.fetch_and_store_articles import (
        AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY,
    )
    enabled = request.form.get("enabled") == "true"
    _save_json_setting(AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY, enabled)
    logger.info(f"[Settings] auto_article_deep_analysis_enabled set to {enabled}")
    return redirect(url_for("admin.tools_page"))


@admin.route("/containers/<name>/restart", methods=["POST"])
@login_required
def restart_container(name):
    force = request.form.get("force") == "true"
    blocking = _blocking_operations_for(name)
    if blocking and not force:
        reason = f"Can't restart '{name}' right now — still running: {_describe_blocking(blocking)}"
        logger.warning("[ContainerRestart] Refused restart of %s: %s", name, reason)
        return redirect(url_for("admin.tools_page", blocked_reason=reason, blocked_target=name))
    if blocking and force:
        logger.warning(
            "[ContainerRestart] Forcing restart of %s despite running: %s",
            name, _describe_blocking(blocking),
        )

    try:
        response = requests.post(f"{DOCKER_RESTART_PROXY_URL}/containers/{name}/restart", timeout=35)
        response.raise_for_status()
        logger.info("[ContainerRestart] Restarted %s", name)
    except requests.RequestException as e:
        logger.error("[ContainerRestart] Failed to restart %s: %s", name, e)
        return redirect(url_for("admin.tools_page", blocked_reason=f"Failed to restart '{name}': {e}"))

    return redirect(url_for("admin.tools_page"))


@admin.route("/containers/restart-all", methods=["POST"])
@login_required
def restart_all_containers():
    force = request.form.get("force") == "true"
    blocking = _running_operations()
    if blocking and not force:
        reason = f"Can't restart all — still running: {_describe_blocking(blocking)}"
        logger.warning("[ContainerRestart] Refused restart-all: %s", reason)
        return redirect(url_for("admin.tools_page", blocked_reason=reason, blocked_target="all"))
    if blocking and force:
        logger.warning(
            "[ContainerRestart] Forcing restart-all despite running: %s",
            _describe_blocking(blocking),
        )

    try:
        response = requests.post(f"{DOCKER_RESTART_PROXY_URL}/restart-all", timeout=120)
        response.raise_for_status()
        logger.info("[ContainerRestart] Restart-all results: %s", response.json())
    except requests.RequestException as e:
        # Expected once the app container itself gets restarted mid-call --
        # the response never makes it back. Log and move on either way.
        logger.info("[ContainerRestart] Restart-all request ended (%s) -- expected if 'app' was restarted.", e)

    return redirect(url_for("admin.tools_page"))


def _is_running_and_fresh(payload):
    """True if a task-status payload is 'running' and still within a
    plausible runtime -- reuses ORPHANED_TASK_STALE_AFTER so a process that
    died mid-task (no restart involved at all) doesn't block restarts
    forever; the next real run overwrites the flag anyway."""
    if not payload or payload.get("status") != "running":
        return False
    started_at = payload.get("started_at")
    try:
        started_dt = datetime.fromisoformat(started_at) if started_at else None
    except (TypeError, ValueError):
        started_dt = None
    if started_dt is None:
        return True
    return started_dt > datetime.utcnow() - ORPHANED_TASK_STALE_AFTER


def _running_operations():
    """
    Every background/long-running operation currently active that a
    container restart could interrupt, each tagged with which of this
    project's containers restarting would interrupt (`affects`). Covers:
    scheduled fetch/pipeline runs, manual on-demand fetches, the six bulk
    admin actions, per-resource AI tasks, and search reindexing.
    """
    ops = []

    fetch_status = _load_json_setting(FETCH_RUN_STATUS_KEY)
    if _is_running_and_fresh(fetch_status):
        kind = "full pipeline" if fetch_status.get("run_full_pipeline") else "fetch-only"
        ops.append({
            "kind": "fetch",
            "label": f"Scheduled {kind} run",
            "affects": {"scheduler", "postgres"},
            "started_at": fetch_status.get("started_at"),
        })

    manual_status = _load_json_setting(MANUAL_FETCH_STATUS_KEY)
    if _is_running_and_fresh(manual_status):
        ops.append({
            "kind": "fetch",
            "label": f"Manual fetch ({manual_status.get('label') or 'Custom'})",
            "affects": {"app", "postgres"},
            "started_at": manual_status.get("started_at"),
        })

    for action in BULK_TASK_ACTIONS:
        payload = _bulk_task_status_payload(action)
        if _is_running_and_fresh(payload):
            ops.append({
                "kind": "other",
                "label": action.replace("_", " ").title(),
                "affects": {"app", "postgres"},
                "started_at": payload.get("started_at"),
            })

    ai_rows = AppSetting.query.filter(AppSetting.key.like("ai_task_status_v1:%")).all()
    for row in ai_rows:
        try:
            payload = json.loads(row.value) if row.value else None
        except (TypeError, ValueError):
            continue
        if _is_running_and_fresh(payload):
            ops.append({
                "kind": "other",
                "label": f"AI task: {payload.get('task_type')} #{payload.get('resource_id')}",
                "affects": {"app", "postgres"},
                "started_at": payload.get("started_at"),
            })

    reindex_status = _search_reindex_status_payload()
    if _is_running_and_fresh(reindex_status):
        ops.append({
            "kind": "other",
            "label": "Search reindex",
            "affects": {"app", "postgres", "meilisearch"},
            "started_at": reindex_status.get("started_at"),
        })

    return ops


def _blocking_operations_for(container_name):
    return [op for op in _running_operations() if container_name in op["affects"]]


def _list_containers():
    """Container status via the docker-restart-proxy. Returns [] (and logs
    a warning) rather than raising if the proxy is unreachable, so a proxy
    hiccup doesn't take down the whole Admin Tools page."""
    try:
        response = requests.get(f"{DOCKER_RESTART_PROXY_URL}/containers", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.warning("[ContainerRestart] Could not reach docker-restart-proxy: %s", e)
        return []


def _describe_blocking(ops):
    return "; ".join(
        f"{op['label']} (started {op['started_at']})" for op in ops
    )
