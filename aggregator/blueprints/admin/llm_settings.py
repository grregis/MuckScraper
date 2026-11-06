"""Settings › LLM providers: which backend serves each tier, what each
provider has configured, and which pipeline tasks run on which tier.

Provider settings are environment variables (news_fetcher/llm_client.py
reads them at import, in both the app and scheduler containers), so this
page shows them rather than editing them. The two things it does change
are the auto deep-analysis and analyse-all switches and a one-line test
call per tier.
"""
import logging
import time

from flask import jsonify, redirect, render_template, request, url_for

from aggregator.permissions import ROLE_ADMIN, role_required

from . import admin
from ._shared import _load_json_setting, _save_json_setting

logger = logging.getLogger(__name__)

PROVIDER_LABELS = {
    "ollama": "Ollama",
    "gemini": "Gemini",
    "groq": "Groq",
    "openrouter": "OpenRouter",
}

# Which tier each kind of pipeline work uses. Mirrors the generate_text()
# call sites: the high-volume label/yes-no calls are fast, anything a reader
# sees as prose is main (see the TIER_* comments in llm_client.py).
# This list is kept by hand. When a generate_text(tier=...) call site is added
# or moved, update its row here too, or this page will show the old tier.
TASK_ROUTES = [
    ("Article and story summaries", "quality"),
    ("Deep reports and article analysis", "quality"),
    ("Edition headlines", "quality"),
    ("Opinion stance and roundups", "quality"),
    ("Topic classification", "fast"),
    ("Story grouping checks", "fast"),
    ("Outlet bias scoring", "fast"),
    ("Linking opinion to stories", "fast"),
]


def _mask(value):
    """Enough of a key to tell two apart, never enough to use."""
    if not value:
        return ""
    return f"…{value[-4:]}" if len(value) > 8 else "set"


def _provider_rows():
    from news_fetcher import llm_client as llm

    quality, fast = llm.LLM_PROVIDER, llm.LLM_FAST_PROVIDER
    embedding = llm.EMBEDDING_PROVIDER

    def uses(name):
        roles = []
        if name == quality:
            roles.append("Main")
        if name == fast:
            roles.append("Fast")
        if name == embedding:
            roles.append("Embeddings")
        return roles

    rows = []

    hosts = [h for h in (llm.OLLAMA_HOST, llm.OLLAMA_FALLBACK_HOST) if h]
    rows.append({
        "key": "ollama",
        "name": "Ollama",
        "roles": uses("ollama"),
        "configured": bool(llm.OLLAMA_HOST and llm.OLLAMA_MODEL),
        "facts": [
            ("Primary host", llm.OLLAMA_HOST or "Not set"),
            ("Fallback host", llm.OLLAMA_FALLBACK_HOST or "None"),
            ("Main model", llm.OLLAMA_MODEL or "Not set"),
            ("Fast model", llm.OLLAMA_FAST_MODEL or "Not set"),
            ("Embedding model", llm.EMBEDDING_MODEL),
        ],
        "env": "OLLAMA_HOST, OLLAMA_FALLBACK_HOST, OLLAMA_MODEL, OLLAMA_FAST_MODEL",
        "live": bool(hosts),
    })
    rows.append({
        "key": "gemini",
        "name": "Gemini",
        "roles": uses("gemini"),
        "configured": bool(llm.GEMINI_API_KEY),
        "facts": [
            ("API key", _mask(llm.GEMINI_API_KEY) or "Not set"),
            ("Main model", llm.GEMINI_MODEL),
            ("Fast model", llm.GEMINI_FAST_MODEL),
            ("Embedding model", llm.GEMINI_EMBEDDING_MODEL),
        ],
        "env": "GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FAST_MODEL",
    })
    for key in llm.OPENAI_COMPATIBLE_PROVIDERS:
        config = llm._openai_compatible_config(key)
        rows.append({
            "key": key,
            "name": config["label"],
            "roles": uses(key),
            "configured": bool(config["api_key"] and config["model"]),
            "facts": [
                ("API key", _mask(config["api_key"]) or "Not set"),
                ("Main model", config["model"] or "Not set"),
                ("Fast model", config["fast_model"] or "Not set"),
                ("Endpoint", config["base_url"]),
            ],
            "env": f"{key.upper()}_API_KEY, {key.upper()}_MODEL, {key.upper()}_FAST_MODEL",
        })

    # Providers in use first (main, then fast, then embeddings), the rest after.
    def order(row):
        r = row["roles"]
        return (0 if "Main" in r else 1 if "Fast" in r else 2 if r else 3)
    rows.sort(key=order)
    return rows


@admin.route("/settings/llm")
@role_required(ROLE_ADMIN)
def llm_providers_page():
    from news_fetcher import llm_client as llm
    AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY = "auto_article_deep_analysis_enabled"
    from news_fetcher.fetch_and_store_articles.edition_content import (
        _env_flag, analyze_all_stories_setting,
    )
    analyze_all_enabled, analyze_all_source = analyze_all_stories_setting()
    from news_fetcher.backfill import needs_paid_ack

    tiers = {
        "quality": {
            "provider": llm.provider_for_tier(llm.TIER_QUALITY),
            "model": llm.model_for_tier(llm.TIER_QUALITY),
            "configured": llm.is_configured(llm.TIER_QUALITY),
        },
        "fast": {
            "provider": llm.provider_for_tier(llm.TIER_FAST),
            "model": llm.model_for_tier(llm.TIER_FAST),
            "configured": llm.is_configured(llm.TIER_FAST),
        },
    }
    routes = [
        {"task": task, "tier": tier, **tiers[tier]}
        for task, tier in TASK_ROUTES
    ]
    return render_template(
        "settings_llm.html",
        providers=_provider_rows(),
        provider_labels=PROVIDER_LABELS,
        tiers=tiers,
        routes=routes,
        embedding_provider=llm.EMBEDDING_PROVIDER,
        auto_article_deep_analysis_enabled=bool(_load_json_setting(AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY)),
        analyze_all_enabled=analyze_all_enabled,
        analyze_all_source=analyze_all_source,
        analyze_all_env=_env_flag("ANALYZE_ALL_STORIES"),
        backfill_needs_ack=needs_paid_ack(tiers["quality"]["provider"]),
    )


@admin.route("/settings/llm/analyze-all", methods=["POST"])
@role_required(ROLE_ADMIN)
def toggle_analyze_all_stories():
    """The analyse-all switch. \"reset\" forgets it, so ANALYZE_ALL_STORIES
    in .env decides again."""
    from aggregator import db
    from aggregator.models import AppSetting
    from news_fetcher.fetch_and_store_articles import ANALYZE_ALL_STORIES_SETTING_KEY
    if request.form.get("reset") == "true":
        AppSetting.query.filter_by(key=ANALYZE_ALL_STORIES_SETTING_KEY).delete()
        db.session.commit()
        logger.info("[Settings] analyze_all_stories reset to the .env value")
    else:
        enabled = request.form.get("enabled") == "true"
        _save_json_setting(ANALYZE_ALL_STORIES_SETTING_KEY, enabled)
        logger.info(f"[Settings] analyze_all_stories set to {enabled}")
    return redirect(url_for("admin.llm_providers_page") + "#analyze-all")


@admin.route("/settings/llm/backfill")
@role_required(ROLE_ADMIN)
def backfill_status():
    """Progress of the summary/analysis backfill, polled by the page."""
    from news_fetcher import backfill
    status = backfill.get_status()
    status["pending_start"] = backfill.get_control().get("command") == "start"
    return jsonify(status)


@admin.route("/settings/llm/backfill", methods=["POST"])
@role_required(ROLE_ADMIN)
def backfill_control():
    """Start (newest or oldest first) or stop the backfill. The scheduler
    runs it; this only records the request."""
    from news_fetcher import backfill
    data = request.get_json(silent=True) or {}
    if data.get("action") == "stop":
        ok, message = backfill.request_stop("admin")
    else:
        ok, message = backfill.request_start(
            data.get("order"), "admin", paid_ack=bool(data.get("paid_ack")))
    logger.info(f"[Settings] backfill {data.get('action', 'start')}: {message}")
    return jsonify({"ok": ok, "message": message})


@admin.route("/settings/llm/test", methods=["POST"])
@role_required(ROLE_ADMIN)
def llm_test():
    """One short generate call on a tier, so a key or model change can be
    checked without waiting for the next pipeline run."""
    from news_fetcher import llm_client as llm

    from . import FETCH_RUN_STATUS_KEY
    from .tools import _is_running_and_fresh

    tier = llm.TIER_FAST if (request.get_json(silent=True) or {}).get("tier") == "fast" else llm.TIER_QUALITY
    provider = llm.provider_for_tier(tier)
    if not llm.is_configured(tier):
        return jsonify({"ok": False, "provider": provider, "message": "Not configured."})
    # A test during a run makes Ollama swap models and skews that run's timings.
    if _is_running_and_fresh(_load_json_setting(FETCH_RUN_STATUS_KEY)):
        return jsonify({"ok": False, "provider": provider,
                        "message": "A pipeline run is in progress. Test again when it finishes."})
    # A sleeping Ollama box would otherwise make the test wait out its full timeout.
    if provider == "ollama":
        entry = next((e for e in llm.llm_status_detail()["providers"] if e["provider"] == "ollama"), None)
        if entry and not entry["online"]:
            return jsonify({"ok": False, "provider": provider,
                            "message": "Ollama is offline (asleep or unreachable)."})
    started = time.monotonic()
    try:
        reply = llm.generate_text("Reply with the single word OK.", timeout=30, tier=tier)
    except Exception as e:  # any provider error is the answer here
        logger.warning("[LLM test] %s (%s) failed: %s", provider, tier, e)
        reply = None
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not reply:
        return jsonify({"ok": False, "provider": provider, "message": f"No reply after {elapsed_ms} ms."})
    return jsonify({
        "ok": True, "provider": provider, "model": llm.model_for_tier(tier),
        "message": f"Replied in {elapsed_ms} ms.",
    })
