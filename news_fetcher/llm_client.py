# news_fetcher/llm_client.py
#
# Thin dispatch layer so the rest of the pipeline doesn't care whether the
# backend is a home Ollama box, Gemini, or any OpenAI-compatible endpoint
# (Groq, OpenRouter, and through OpenRouter's base URL anything else speaking
# that API). Selected independently for text generation and embeddings via
# LLM_PROVIDER / EMBEDDING_PROVIDER so either can be flipped back to "ollama"
# with no code changes once Ollama is reachable again.
#
# Note the asymmetry: every provider here can generate text, but only Ollama
# and Gemini can produce embeddings -- see get_embedding().

import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()
# Optional separate provider for the fast tier, so the ~1,140 mechanical calls
# in a run can go somewhere different from the ~65 summary/deep-report calls --
# e.g. a local Ollama box for classification with a cloud model for summaries.
# Blank means "same as LLM_PROVIDER", exactly the convention OLLAMA_FAST_MODEL
# and friends already use, so an install that only ever set LLM_PROVIDER keeps
# sending everything to one backend.
LLM_FAST_PROVIDER = os.environ.get("LLM_FAST_PROVIDER", "").strip().lower() or LLM_PROVIDER
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "ollama").strip().lower()

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "")
# Optional second Ollama host used when OLLAMA_HOST is unreachable -- e.g. an
# always-on CPU box that covers for a Wake-on-LAN GPU box while it sleeps or
# wakes. Leave blank to keep the original single-host behavior unchanged.
OLLAMA_FALLBACK_HOST = os.environ.get("OLLAMA_FALLBACK_HOST", "")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")

# Optional smaller/faster model for the pipeline's high-volume mechanical calls
# (story-grouping confirmation, topic classification, headline generation,
# outlet bias) -- see generate_text()'s `tier` argument. A full pipeline run
# makes ~1,100 of those against ~65 summary/deep-report calls, so this is where
# nearly all the wall-clock goes. Leave blank to use OLLAMA_MODEL for
# everything, which is exactly the old single-model behavior.
OLLAMA_FAST_MODEL = os.environ.get("OLLAMA_FAST_MODEL", "") or OLLAMA_MODEL

# How long to keep serving from the fallback host before re-probing whether the
# primary has come back online (issue #7's "detects that it comes online").
OLLAMA_PRIMARY_RECHECK_SECONDS = int(
    os.environ.get("OLLAMA_PRIMARY_RECHECK_SECONDS", "60")
)
# The reachability probe must stay short: a sleeping primary that never answers
# its SYN would otherwise stall behind a real call's full generate timeout.
_OLLAMA_PROBE_TIMEOUT = 5

# Shared host-health state so most calls skip a known-down primary instead of
# paying its connect timeout every time. A race here costs at most one extra
# probe, so no lock is needed.
_ollama_state = {"active": None, "next_primary_probe": 0.0}

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_FAST_MODEL = os.environ.get("GEMINI_FAST_MODEL", "") or GEMINI_MODEL
GEMINI_EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
# No default model name on purpose. This used to default to
# "llama-3.1-8b-instant", which Groq has since retired -- so the default sent
# every call to a model that 404s, and the failure surfaced as a generic
# generate error rather than a config problem. An unset value now reads as
# "not configured" via is_configured(), which fails once and legibly.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "")
GROQ_FAST_MODEL = os.environ.get("GROQ_FAST_MODEL", "") or GROQ_MODEL
GROQ_HOST = os.environ.get("GROQ_HOST", "https://api.groq.com/openai/v1")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "")
OPENROUTER_FAST_MODEL = os.environ.get("OPENROUTER_FAST_MODEL", "") or OPENROUTER_MODEL
OPENROUTER_HOST = os.environ.get("OPENROUTER_HOST", "https://openrouter.ai/api/v1")
# OpenRouter uses these purely for attribution on its public leaderboards; both
# are optional and sending neither is fine.
OPENROUTER_SITE_URL = os.environ.get("OPENROUTER_SITE_URL", "")
OPENROUTER_APP_NAME = os.environ.get("OPENROUTER_APP_NAME", "")


def _openrouter_headers():
    headers = {}
    if OPENROUTER_SITE_URL:
        headers["HTTP-Referer"] = OPENROUTER_SITE_URL
    if OPENROUTER_APP_NAME:
        headers["X-Title"] = OPENROUTER_APP_NAME
    return headers


# Providers that speak the OpenAI `chat/completions` API. Adding one is a
# config entry, not a code path: any endpoint with that shape -- DeepSeek,
# OpenAI itself, Together, a local vLLM -- works by pointing *_HOST at it.
#
# Read through _openai_compatible_config() rather than directly, so tests can
# monkeypatch the module-level vars the way they already do for Ollama/Gemini.
OPENAI_COMPATIBLE_PROVIDERS = ("groq", "openrouter")


def _openai_compatible_config(provider):
    if provider == "groq":
        return {
            "base_url": GROQ_HOST,
            "api_key": GROQ_API_KEY,
            "model": GROQ_MODEL,
            "fast_model": GROQ_FAST_MODEL,
            "headers": {},
            "label": "Groq",
        }
    if provider == "openrouter":
        return {
            "base_url": OPENROUTER_HOST,
            "api_key": OPENROUTER_API_KEY,
            "model": OPENROUTER_MODEL,
            "fast_model": OPENROUTER_FAST_MODEL,
            "headers": _openrouter_headers(),
            "label": "OpenRouter",
        }
    return None


# Tiers accepted by generate_text(). "quality" is the default so any existing
# call site keeps using the main model until it is explicitly opted in.
TIER_QUALITY = "quality"
TIER_FAST = "fast"


def provider_for_tier(tier=TIER_QUALITY):
    """Which provider serves a given tier.

    The counterpart to model_for_tier(): that picks a model *within* a
    provider, this picks the provider itself. An unrecognized tier resolves to
    the quality provider, matching model_for_tier()'s degrade-to-quality rule.
    """
    return LLM_FAST_PROVIDER if tier == TIER_FAST else LLM_PROVIDER


def providers_in_use():
    """Distinct providers this install actually routes to, quality first.

    One entry for every existing install, since LLM_FAST_PROVIDER defaults to
    LLM_PROVIDER -- callers should render per-provider UI off this rather than
    off the tier count, or a single-provider setup shows two identical rows.
    """
    providers = [LLM_PROVIDER]
    if LLM_FAST_PROVIDER != LLM_PROVIDER:
        providers.append(LLM_FAST_PROVIDER)
    return providers

_gemini_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def is_configured(tier=TIER_QUALITY):
    """Whether the provider serving `tier` has enough config to be called.

    Provider-agnostic replacement for the old `if OLLAMA_HOST:` gates that
    decided whether an LLM-backed step should run at all.

    The tier argument matters under split routing: every current caller is a
    fast-tier call site (story grouping, topic classification), so checking
    the global provider would gate their work on a backend they never use.
    Defaults to quality so existing callers are unchanged.
    """
    provider = provider_for_tier(tier)
    if provider == "gemini":
        return bool(GEMINI_API_KEY)
    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        config = _openai_compatible_config(provider)
        # Both providers in this family require an explicit model name.
        # Neither has a default that stays valid -- provider catalogues change
        # under you (Groq retired the old llama-3.1-8b-instant default) -- so
        # an unset model must read as "not configured" rather than send an
        # empty or dead model name on every call.
        return bool(config["api_key"] and config["model"])
    return bool(OLLAMA_HOST and OLLAMA_MODEL)


def model_for_tier(tier=TIER_QUALITY):
    """Which model name the active provider uses for a given tier.

    Falls back to the quality model for an unrecognized tier so a typo degrades
    to the old behavior (slower but correct) instead of sending a bad model
    name the provider would reject."""
    fast = tier == TIER_FAST
    provider = provider_for_tier(tier)
    if provider == "gemini":
        return GEMINI_FAST_MODEL if fast else GEMINI_MODEL
    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        config = _openai_compatible_config(provider)
        return config["fast_model"] if fast else config["model"]
    return OLLAMA_FAST_MODEL if fast else OLLAMA_MODEL


def generate_text(prompt, timeout=30, tier=TIER_QUALITY):
    """Generate text with the active provider.

    `tier` selects between the main model and the optional faster one:
    TIER_FAST for high-volume mechanical calls whose output is a label, a
    yes/no, or a short headline; TIER_QUALITY (the default) for summaries and
    deep reports, where the larger model's output is what readers actually see.
    """
    provider = provider_for_tier(tier)
    model = model_for_tier(tier)
    if provider == "gemini":
        return _generate_text_gemini(prompt, timeout, model)
    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        return _generate_text_openai_compatible(prompt, timeout, model, provider)
    return _generate_text_ollama(prompt, timeout, model)


def _probe_ollama_host(host):
    """Cheap reachability check (GET /api/tags) used by the fallback selector
    and the admin status surface. Kept short via _OLLAMA_PROBE_TIMEOUT."""
    if not host:
        return False
    try:
        response = requests.get(f"{host}/api/tags", timeout=_OLLAMA_PROBE_TIMEOUT)
        return response.status_code == 200
    except Exception:
        return False


def _ollama_host_candidates():
    """Ordered hosts to try for the next Ollama call, preferring the primary
    when it is known-good and the fallback otherwise.

    Steps (order matters):
    1. No fallback configured -> return just the primary, so behavior is
       identical to the old single-host code for anyone who never sets
       OLLAMA_FALLBACK_HOST.
    2. On first use, or while parked on the fallback past the recheck window,
       do ONE cheap probe of the primary. This avoids gambling a long generate
       timeout on a sleeping GPU box AND lets us auto-recover to the primary
       once it wakes.
    3. Return the preferred host first and the other as a last-resort retry.
    """
    primary = OLLAMA_HOST
    fallback = OLLAMA_FALLBACK_HOST
    if not fallback:
        return [primary]

    now = time.monotonic()
    active = _ollama_state["active"]
    if active is None or (
        active != "primary" and now >= _ollama_state["next_primary_probe"]
    ):
        _ollama_state["next_primary_probe"] = now + OLLAMA_PRIMARY_RECHECK_SECONDS
        active = _ollama_state["active"] = (
            "primary" if _probe_ollama_host(primary) else "fallback"
        )

    if active == "fallback":
        return [fallback, primary]
    return [primary, fallback]


def _mark_ollama_host_up(host):
    if host == OLLAMA_HOST:
        _ollama_state["active"] = "primary"
    elif host == OLLAMA_FALLBACK_HOST:
        _ollama_state["active"] = "fallback"
        _ollama_state["next_primary_probe"] = (
            time.monotonic() + OLLAMA_PRIMARY_RECHECK_SECONDS
        )


def _mark_ollama_host_down(host):
    # If the primary failed mid-request, park on the fallback and defer the next
    # primary probe rather than retrying the dead primary on every subsequent call.
    if host == OLLAMA_HOST and OLLAMA_FALLBACK_HOST:
        _ollama_state["active"] = "fallback"
        _ollama_state["next_primary_probe"] = (
            time.monotonic() + OLLAMA_PRIMARY_RECHECK_SECONDS
        )


def _ollama_request(kind, fn):
    """Run fn(host) against the preferred Ollama host, falling back to the other
    host on failure, and update the shared health state. Returns fn's result, or
    None if every candidate failed. `kind` is a label used only for logging."""
    for host in _ollama_host_candidates():
        if not host:
            continue
        try:
            result = fn(host)
        except Exception as e:
            logger.info(f"  [llm_client] Ollama {kind} error on {host}: {e}")
            _mark_ollama_host_down(host)
            continue
        _mark_ollama_host_up(host)
        if host == OLLAMA_FALLBACK_HOST and host != OLLAMA_HOST:
            logger.info(f"  [llm_client] Ollama {kind} served by fallback host {host}")
        return result
    return None


def _generate_text_ollama(prompt, timeout, model=None):
    if not OLLAMA_HOST and not OLLAMA_FALLBACK_HOST:
        return None

    model = model or OLLAMA_MODEL

    def _call(host):
        response = requests.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()

    return _ollama_request("generate", _call)


def _generate_text_gemini(prompt, timeout, model=None):
    if not GEMINI_API_KEY:
        return None
    try:
        client = _get_gemini_client()
        response = client.models.generate_content(
            model=model or GEMINI_MODEL,
            contents=prompt,
        )
        text = (response.text or "").strip()
        return text or None
    except Exception as e:
        logger.info(f"  [llm_client] Gemini generate error: {e}")
        return None


GROQ_MAX_RETRIES = 2
GROQ_MAX_RETRY_WAIT_SECONDS = 65
# Kept under the old Groq-specific names because the retry behavior and the
# tuning behind it are unchanged; they now apply to every OpenAI-compatible
# provider, which is what the 429 handling was always really about.
OPENAI_COMPATIBLE_MAX_RETRIES = GROQ_MAX_RETRIES
OPENAI_COMPATIBLE_MAX_RETRY_WAIT_SECONDS = GROQ_MAX_RETRY_WAIT_SECONDS


def _generate_text_openai_compatible(prompt, timeout, model, provider, _attempt=0):
    """One implementation of the OpenAI `chat/completions` shape, shared by
    every provider that speaks it.

    Groq and OpenRouter differ only in base URL, key and optional headers, so
    they are config rather than separate functions -- which is what makes "any
    OpenAI-compatible endpoint" work without new code.
    """
    config = _openai_compatible_config(provider)
    if not config or not config["api_key"]:
        return None
    model = model or config["model"]
    label = config["label"]
    headers = {"Authorization": f"Bearer {config['api_key']}", **config["headers"]}
    try:
        response = requests.post(
            f"{config['base_url'].rstrip('/')}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        if response.status_code == 429 and _attempt < OPENAI_COMPATIBLE_MAX_RETRIES:
            # A 429 from these providers is usually a per-minute window rather
            # than a hard quota -- Groq's free tier caps at 6,000 tokens/minute,
            # which a single large summary prompt can exceed on its own -- so
            # waiting it out works, unlike Gemini's per-day quota where a retry
            # is futile.
            wait_seconds = min(
                float(response.headers.get("Retry-After", 15)) + 1,
                OPENAI_COMPATIBLE_MAX_RETRY_WAIT_SECONDS,
            )
            logger.info(f"  [llm_client] {label} rate-limited, retrying in {wait_seconds:.0f}s")
            time.sleep(wait_seconds)
            return _generate_text_openai_compatible(
                prompt, timeout, model, provider, _attempt=_attempt + 1
            )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        return (text or "").strip() or None
    except Exception as e:
        logger.info(f"  [llm_client] {label} generate error: {e}")
        return None


def _generate_text_groq(prompt, timeout, model=None, _attempt=0):
    """Retained as a named entry point: it predates the generalization and is
    referenced by existing tests and call sites."""
    return _generate_text_openai_compatible(prompt, timeout, model, "groq", _attempt=_attempt)


def _generate_text_openrouter(prompt, timeout, model=None, _attempt=0):
    return _generate_text_openai_compatible(prompt, timeout, model, "openrouter", _attempt=_attempt)


# Providers with no embedding endpoint at all. Kept separate from a plain
# "unknown value" so the warning can say *why* rather than just "unrecognized".
_NO_EMBEDDING_PROVIDERS = {
    "groq": "Groq serves no embedding models",
    "openrouter": "OpenRouter routes chat completions only, not embeddings",
}
_warned_embedding_providers = set()


def get_embedding(text):
    """Embed `text` with EMBEDDING_PROVIDER, falling back to Ollama.

    The fallback is deliberate and long-standing, but it used to be silent,
    which is a trap now that LLM_PROVIDER accepts providers that cannot embed:
    setting EMBEDDING_PROVIDER to match would quietly keep using Ollama's
    vectors while looking configured. Warn once per provider instead of
    per call -- this runs for every ingested article.

    Note that switching embedding providers for real is never just a config
    change: vectors from different models aren't comparable, so it needs a
    re-embed of the whole corpus (and a migration if the dimensions differ
    from Story.embedding's Vector(768)) or story grouping silently degrades.
    """
    if EMBEDDING_PROVIDER == "gemini":
        return _get_embedding_gemini(text)
    if EMBEDDING_PROVIDER not in ("ollama", "") and EMBEDDING_PROVIDER not in _warned_embedding_providers:
        _warned_embedding_providers.add(EMBEDDING_PROVIDER)
        reason = _NO_EMBEDDING_PROVIDERS.get(
            EMBEDDING_PROVIDER, f"'{EMBEDDING_PROVIDER}' is not a recognized embedding provider"
        )
        logger.warning(
            "  [llm_client] EMBEDDING_PROVIDER=%s but %s -- using Ollama for embeddings. "
            "Set EMBEDDING_PROVIDER=ollama or gemini to silence this.",
            EMBEDDING_PROVIDER, reason,
        )
    return _get_embedding_ollama(text)


def _get_embedding_ollama(text):
    if not OLLAMA_HOST and not OLLAMA_FALLBACK_HOST:
        return None

    def _call(host):
        response = requests.post(
            f"{host}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("embedding") or None

    return _ollama_request("embedding", _call)


def _get_embedding_gemini(text):
    if not GEMINI_API_KEY:
        return None
    try:
        import numpy as np
        from google.genai import types

        client = _get_gemini_client()
        response = client.models.embed_content(
            model=GEMINI_EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=768),
        )
        embedding = response.embeddings[0].values
        # gemini-embedding-001 does not auto-normalize truncated output the
        # way the model's native 3072-dim vectors are normalized, so an
        # output_dimensionality=768 result needs manual L2 renormalization.
        vec = np.array(embedding, dtype=float)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()
    except Exception as e:
        logger.info(f"  [llm_client] Gemini embedding error: {e}")
        return None


def _check_provider_status(provider):
    """Liveness for one named provider. Ollama is probed over the network;
    the cloud providers have no cheap health endpoint, so a configured key is
    the best available signal."""
    if provider == "gemini":
        return bool(GEMINI_API_KEY)
    if provider in OPENAI_COMPATIBLE_PROVIDERS:
        return bool(_openai_compatible_config(provider)["api_key"])
    return _check_ollama_status()


def check_llm_status(tier=TIER_QUALITY):
    """Whether the provider serving `tier` is reachable.

    Tier-aware because under split routing a partial outage is the *normal*
    steady state, not a fault: the Ollama box suspends itself after every run,
    so "cloud up, local asleep" happens between every pair of runs. A single
    boolean cannot express "summaries are fine, classification is down", and
    answering it globally would stop the whole pipeline for a backend that
    stage never uses.

    Defaults to quality so any caller not yet passing a tier is unchanged.
    """
    return _check_provider_status(provider_for_tier(tier))


def check_all_llm_status():
    """{provider: online} for each distinct provider in use -- one entry for a
    single-provider install, two when the tiers are split."""
    return {provider: _check_provider_status(provider) for provider in providers_in_use()}


def _check_ollama_status():
    # Online if EITHER host answers -- the fallback keeps AI features available
    # (and the sidebar dot green) while the primary is asleep.
    for host in (OLLAMA_HOST, OLLAMA_FALLBACK_HOST):
        if host and _probe_ollama_host(host):
            return True
    return False


def ollama_host_status():
    """Which Ollama host is currently reachable, for the admin status surface.
    Probes the primary first so the reported role matches what real calls use."""
    if OLLAMA_HOST and _probe_ollama_host(OLLAMA_HOST):
        return {"online": True, "host": OLLAMA_HOST, "role": "primary"}
    if OLLAMA_FALLBACK_HOST and _probe_ollama_host(OLLAMA_FALLBACK_HOST):
        return {"online": True, "host": OLLAMA_FALLBACK_HOST, "role": "fallback"}
    return {"online": False, "host": None, "role": None}


def _provider_status_entry(provider, tier):
    """One provider's status, tagged with the tier it serves. Ollama carries
    host/role so a sleeping primary served by the fallback stays visible."""
    if provider == "ollama":
        entry = ollama_host_status()
    else:
        entry = {"online": _check_provider_status(provider), "host": None, "role": None}
    entry["provider"] = provider
    entry["tier"] = tier
    return entry


def llm_status_detail():
    """Status for the /ollama-status routes.

    `online`, `provider`, `host` and `role` are kept at the top level for the
    sidebar JS and anything else already reading this shape; `providers` is
    the per-backend list, one entry per *distinct* provider rather than per
    tier, so a single-provider install (every existing one) gets exactly one.

    Top-level `online` is the AND across providers: it drives a single
    summary indicator, where "something the pipeline needs is down" is the
    useful meaning.
    """
    tiers_by_provider = {LLM_PROVIDER: TIER_QUALITY}
    if LLM_FAST_PROVIDER != LLM_PROVIDER:
        tiers_by_provider[LLM_FAST_PROVIDER] = TIER_FAST

    providers = [
        _provider_status_entry(provider, tier)
        for provider, tier in tiers_by_provider.items()
    ]
    primary = providers[0]
    return {
        "online": all(entry["online"] for entry in providers),
        "provider": primary["provider"],
        "host": primary["host"],
        "role": primary["role"],
        "providers": providers,
    }


def llm_status_detail_public():
    """llm_status_detail() with host/role stripped from every entry.

    The public /ollama-status route is unauthenticated, and host/role would
    leak internal network details (a LAN IP, and which box is covering for
    which) to anyone who asks.
    """
    detail = llm_status_detail()
    return {
        "online": detail["online"],
        "providers": [
            {"provider": e["provider"], "tier": e["tier"], "online": e["online"]}
            for e in detail["providers"]
        ],
    }
