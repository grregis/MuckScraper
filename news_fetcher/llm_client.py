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

# Output locale directive. Sent as a system message (provider system-slot) so
# the LLM answers in this language even when the upstream prompt is English
# (soft-fork: core prompts stay untouched, only a system output-language line
# is added). Default "de" for this DE downstream. Set LLM_OUTPUT_LANG="" to
# disable (upstream behavior). See LLM-Output-Locale directive.
# Delivered as a system message, NOT prepended to the user prompt, so
# structured-output callers (grouper single-number, classifier topic-name list,
# bias 'unknown'/digit parsers) keep their exact-token instructions unmutated.
LLM_OUTPUT_LANG = os.environ.get("LLM_OUTPUT_LANG", "de").strip().lower()
_LOCALE_SYSTEM = f"Respond in {LLM_OUTPUT_LANG}." if LLM_OUTPUT_LANG else None

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
GEMINI_EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

# Default request timeout (seconds) for short LLM calls - classification, bias
# rating, story grouping, headline generation. Configurable via env so slower
# backends (local large model, Ollama Cloud with higher latency) don't get
# silently degraded by a hardcoded 30s ceiling. Callers that need longer
# (summarizer: 120/180/150) pass an explicit timeout and bypass this default.
# See issue #1.
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "60"))

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


def generate_text(prompt, timeout=None):
    # The output-locale directive is delivered as a system message by each
    # provider's _generate_text_* (not prepended here), so structured-output
    # callers keep their exact-token instructions unmutated.
    if timeout is None:
        timeout = LLM_TIMEOUT
    if LLM_PROVIDER == "gemini":
        return _generate_text_gemini(prompt, timeout)
    if LLM_PROVIDER == "groq":
        return _generate_text_groq(prompt, timeout)
    return _generate_text_ollama(prompt, timeout)


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


def _generate_text_ollama(prompt, timeout):
    if not OLLAMA_HOST and not OLLAMA_FALLBACK_HOST:
        return None

    def _call(host):
        payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
        if _LOCALE_SYSTEM:
            payload["system"] = _LOCALE_SYSTEM
        response = requests.post(
            f"{host}/api/generate",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()

    return _ollama_request("generate", _call)


def _generate_text_gemini(prompt, timeout):
    if not GEMINI_API_KEY:
        return None
    try:
        client = _get_gemini_client()
        kwargs = {"model": GEMINI_MODEL, "contents": prompt}
        if _LOCALE_SYSTEM:
            # system_instruction as a system-slot (google-genai SDK accepts it
            # in the config dict). Keeps the upstream user prompt unmutated.
            kwargs["config"] = {"system_instruction": _LOCALE_SYSTEM}
        response = client.models.generate_content(**kwargs)
        text = (response.text or "").strip()
        return text or None
    except Exception as e:
        logger.info(f"  [llm_client] Gemini generate error: {e}")
        return None


GROQ_MAX_RETRIES = 2
GROQ_MAX_RETRY_WAIT_SECONDS = 65


def _generate_text_groq(prompt, timeout, _attempt=0):
    if not GROQ_API_KEY:
        return None
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": (
                    [{"role": "system", "content": _LOCALE_SYSTEM}, {"role": "user", "content": prompt}]
                    if _LOCALE_SYSTEM
                    else [{"role": "user", "content": prompt}]
                ),
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
            return _generate_text_groq(prompt, timeout, _attempt=_attempt + 1)
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
