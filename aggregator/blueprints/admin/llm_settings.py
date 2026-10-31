"""Settings › LLM providers: which backend serves each tier, what each
provider has configured, and which pipeline tasks run on which tier.

Provider settings are environment variables (news_fetcher/llm_client.py
reads them at import, in both the app and scheduler containers), so this
page shows them rather than editing them. The two things it does change
are the auto deep-analysis switch and a one-line test call per tier.
"""
import logging
import time

from flask import jsonify, render_template, request

from aggregator.permissions import ROLE_ADMIN, role_required

from . import admin
from ._shared import _load_json_setting

logger = logging.getLogger(__name__)

PROVIDER_LABELS = {
    "ollama": "Ollama",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "groq": "Groq",
    "openrouter": "OpenRouter",
}

# Which tier each kind of pipeline work uses. Mirrors the generate_text()
# call sites: the high-volume label/yes-no calls are fast, anything a reader
# sees as prose is main (see the TIER_* comments in llm_client.py).
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
    )


@admin.route("/settings/llm/test", methods=["POST"])
@role_required(ROLE_ADMIN)
def llm_test():
    """One short generate call on a tier, so a key or model change can be
    checked without waiting for the next pipeline run."""
    from news_fetcher import llm_client as llm

    tier = llm.TIER_FAST if (request.get_json(silent=True) or {}).get("tier") == "fast" else llm.TIER_QUALITY
    provider = llm.provider_for_tier(tier)
    if not llm.is_configured(tier):
        return jsonify({"ok": False, "provider": provider, "message": "Not configured."})
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
