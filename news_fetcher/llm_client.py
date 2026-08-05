# news_fetcher/llm_client.py
#
# Thin dispatch layer so the rest of the pipeline doesn't care whether the
# backend is a home Ollama box or Gemini. Selected independently for text
# generation and embeddings via LLM_PROVIDER / EMBEDDING_PROVIDER so either
# can be flipped back to "ollama" with no code changes once Ollama is
# reachable again.

import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()
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
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_FAST_MODEL = os.environ.get("GROQ_FAST_MODEL", "") or GROQ_MODEL

# Tiers accepted by generate_text(). "quality" is the default so any existing
# call site keeps using the main model until it is explicitly opted in.
TIER_QUALITY = "quality"
TIER_FAST = "fast"

_gemini_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def is_configured():
    """Provider-agnostic replacement for the old `if OLLAMA_HOST:` gates
    that decided whether an LLM-backed step should run at all."""
    if LLM_PROVIDER == "gemini":
        return bool(GEMINI_API_KEY)
    if LLM_PROVIDER == "groq":
        return bool(GROQ_API_KEY)
    return bool(OLLAMA_HOST and OLLAMA_MODEL)


def model_for_tier(tier=TIER_QUALITY):
    """Which model name the active provider uses for a given tier.

    Falls back to the quality model for an unrecognized tier so a typo degrades
    to the old behavior (slower but correct) instead of sending a bad model
    name the provider would reject."""
    fast = tier == TIER_FAST
    if LLM_PROVIDER == "gemini":
        return GEMINI_FAST_MODEL if fast else GEMINI_MODEL
    if LLM_PROVIDER == "groq":
        return GROQ_FAST_MODEL if fast else GROQ_MODEL
    return OLLAMA_FAST_MODEL if fast else OLLAMA_MODEL


def generate_text(prompt, timeout=30, tier=TIER_QUALITY):
    """Generate text with the active provider.

    `tier` selects between the main model and the optional faster one:
    TIER_FAST for high-volume mechanical calls whose output is a label, a
    yes/no, or a short headline; TIER_QUALITY (the default) for summaries and
    deep reports, where the larger model's output is what readers actually see.
    """
    model = model_for_tier(tier)
    if LLM_PROVIDER == "gemini":
        return _generate_text_gemini(prompt, timeout, model)
    if LLM_PROVIDER == "groq":
        return _generate_text_groq(prompt, timeout, model)
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


def _generate_text_groq(prompt, timeout, model=None, _attempt=0):
    if not GROQ_API_KEY:
        return None
    model = model or GROQ_MODEL
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        if response.status_code == 429 and _attempt < GROQ_MAX_RETRIES:
            # Groq's free tier is limited to 6,000 tokens/minute (TPM), which
            # large summary/deep-report prompts can exceed on their own -- a
            # 429 here usually just means "wait out this minute's window",
            # unlike Gemini's per-day quota where retrying is futile.
            wait_seconds = min(
                float(response.headers.get("Retry-After", 15)) + 1,
                GROQ_MAX_RETRY_WAIT_SECONDS,
            )
            logger.info(f"  [llm_client] Groq rate-limited, retrying in {wait_seconds:.0f}s")
            time.sleep(wait_seconds)
            return _generate_text_groq(prompt, timeout, model, _attempt=_attempt + 1)
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        return (text or "").strip() or None
    except Exception as e:
        logger.info(f"  [llm_client] Groq generate error: {e}")
        return None


def get_embedding(text):
    if EMBEDDING_PROVIDER == "gemini":
        return _get_embedding_gemini(text)
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


def check_llm_status():
    if LLM_PROVIDER == "gemini":
        return bool(GEMINI_API_KEY)
    if LLM_PROVIDER == "groq":
        return bool(GROQ_API_KEY)
    return _check_ollama_status()


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


def llm_status_detail():
    """Provider-aware status for the /ollama-status route: keeps the `online`
    boolean the sidebar JS already depends on, plus host/role for Ollama."""
    if LLM_PROVIDER == "gemini":
        return {"online": bool(GEMINI_API_KEY), "provider": "gemini", "host": None, "role": None}
    if LLM_PROVIDER == "groq":
        return {"online": bool(GROQ_API_KEY), "provider": "groq", "host": None, "role": None}
    info = ollama_host_status()
    info["provider"] = "ollama"
    return info
