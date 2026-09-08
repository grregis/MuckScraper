import os
from datetime import timedelta
from flask import Blueprint

admin = Blueprint("admin", __name__)

SEARCH_REINDEX_STATUS_KEY = "search_reindex_status_v1"
MANUAL_FETCH_STATUS_KEY = "manual_fetch_status_v1"
# Matches news_fetcher/scheduler.py's FETCH_RUN_STATUS_KEY -- duplicated
# rather than imported to avoid pulling the scheduler module (and its own
# app/db wiring) into the admin app just for one string constant.
FETCH_RUN_STATUS_KEY = "fetch_run_status_v1"

DOCKER_RESTART_PROXY_URL = os.environ.get("DOCKER_RESTART_PROXY_URL", "http://docker-restart-proxy:5001")
CONTAINER_SERVICE_NAMES = ["postgres", "meilisearch", "scheduler", "app"]
SCRAPE_STATUS_FILTERS = ("success", "fallback", "blocked", "failed", "skipped", "pending")

# AppSetting key prefixes whose value is a background-task status blob.
_TASK_STATUS_KEY_PREFIXES = ("bulk_task_status_v1:", "ai_task_status_v1:")

# How long a task-status can plausibly stay 'running' before it's treated as
# orphaned rather than just slow. Generous on purpose -- some of these
# (force_regroup_all, reclassify_all_articles) can legitimately run for a long
# time on a full DB; this only needs to catch tasks that are truly dead, not
# flag slow-but-alive ones.
ORPHANED_TASK_STALE_AFTER = timedelta(hours=3)

BULK_TASK_ACTIONS = {
    "ollama_catchup",
    "scrape_all_missing",
    "force_regroup",
    "force_resummarize",
    "reclassify_articles",
    "audit_scrapes",
}

# Submodule imports must come after the constants above (they depend on
# them) and after `admin` is defined (each submodule does `from . import
# admin` and decorates routes onto that same Blueprint object). Import
# order among them matters only where one depends on another's names at
# import time -- config_crud before tools, since tools.py imports
# pipeline_schedule_restart_needed from it.
from . import _shared, _tasks, articles, bulk_actions, scrape_blocklist, config_crud, tools  # noqa: E402,F401

# Re-exported because aggregator/app.py does
# `from aggregator.blueprints.admin import reconcile_orphaned_task_statuses`.
from ._tasks import reconcile_orphaned_task_statuses  # noqa: E402,F401
