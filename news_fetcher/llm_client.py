# news_fetcher/llm_client.py
#
# Thin dispatch layer so the rest of the pipeline doesn't care whether the
# backend is a home Ollama box or Gemini. Selected independently for text
# generation and embeddings via LLM_PROVIDER / EMBEDDING_PROVIDER so either
# can be flipped back to "ollama" with no code changes once Ollama is
# reachable again.

import os
import logging
import requests

logger = logging.getLogger(__name__)

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "ollama").strip().lower()

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

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


def generate_text(prompt, timeout=30):
    if LLM_PROVIDER == "gemini":
        return _generate_text_gemini(prompt, timeout)
    if LLM_PROVIDER == "groq":
        return _generate_text_groq(prompt, timeout)
    return _generate_text_ollama(prompt, timeout)


def _generate_text_ollama(prompt, timeout):
    if not OLLAMA_HOST:
        return None
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except Exception as e:
        logger.info(f"  [llm_client] Ollama generate error: {e}")
        return None


def _generate_text_gemini(prompt, timeout):
    if not GEMINI_API_KEY:
        return None
    try:
        client = _get_gemini_client()
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = (response.text or "").strip()
        return text or None
    except Exception as e:
        logger.info(f"  [llm_client] Gemini generate error: {e}")
        return None


def _generate_text_groq(prompt, timeout):
    if not GROQ_API_KEY:
        return None
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
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
    if not OLLAMA_HOST:
        return None
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("embedding") or None
    except Exception as e:
        logger.info(f"  [llm_client] Ollama embedding error: {e}")
        return None


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
    try:
        response = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        return response.status_code == 200
    except Exception:
        return False
