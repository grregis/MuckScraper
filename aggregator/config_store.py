"""Settings saved from the admin UI: LLM providers (Settings › LLM
providers) and news source API keys (Settings › Source API keys).

Each setting overrides the environment variable of the same name; a cleared
setting falls back to the environment again. API keys are encrypted with
aggregator/secret_box.py and never sent back to the browser: a page only
sees whether one is saved and its last four characters.

install() hands config_source() to news_fetcher/saved_settings.py, which
the app and scheduler read through (saved_settings.get(name), and
llm_client for the provider settings). The database is read at most every
RELOAD_SECONDS, so a change reaches the pipeline within that time without a
restart.
"""
import logging
import os
import re
import time
from datetime import datetime

from flask import has_app_context
from sqlalchemy import text

from aggregator import db, secret_box
from aggregator.models import ConfigSetting

logger = logging.getLogger(__name__)

RELOAD_SECONDS = 15

# Per provider, the settings the page edits: (name, label, kind).
# kind: "secret" (encrypted), "url" (http/https), "model" (a model name).
PROVIDER_FIELDS = {
    "ollama": [
        ("OLLAMA_HOST", "Primary host", "url"),
        ("OLLAMA_FALLBACK_HOST", "Fallback host", "url"),
        ("OLLAMA_MODEL", "Main model", "model"),
        ("OLLAMA_FAST_MODEL", "Fast model", "model"),
    ],
    "anthropic": [
        ("ANTHROPIC_API_KEY", "API key", "secret"),
        ("ANTHROPIC_MODEL", "Main model", "model"),
        ("ANTHROPIC_FAST_MODEL", "Fast model", "model"),
    ],
    "gemini": [
        ("GEMINI_API_KEY", "API key", "secret"),
        ("GEMINI_MODEL", "Main model", "model"),
        ("GEMINI_FAST_MODEL", "Fast model", "model"),
    ],
    "groq": [
        ("GROQ_API_KEY", "API key", "secret"),
        ("GROQ_MODEL", "Main model", "model"),
        ("GROQ_FAST_MODEL", "Fast model", "model"),
    ],
    "openrouter": [
        ("OPENROUTER_API_KEY", "API key", "secret"),
        ("OPENROUTER_MODEL", "Main model", "model"),
        ("OPENROUTER_FAST_MODEL", "Fast model", "model"),
    ],
}
ROUTING_FIELDS = ("LLM_PROVIDER", "LLM_FAST_PROVIDER")

# News source APIs (Settings › Source API keys), same shape as PROVIDER_FIELDS.
SOURCE_FIELDS = {
    "newsapi": [("NEWS_API_KEY", "API key", "secret")],
    "gnews": [("GNEWS_API_KEY", "API key", "secret")],
}

# Defaults the code applies when neither a saved value nor the environment
# sets one, shown as the field's placeholder.
DEFAULTS = {"NYT_API_DAILY_LIMIT": "450"}

_FIELD_GROUPS = list(PROVIDER_FIELDS.values()) + list(SOURCE_FIELDS.values())
SECRET_NAMES = {name for fields in _FIELD_GROUPS for name, _, kind in fields if kind == "secret"}
ALL_NAMES = set(ROUTING_FIELDS) | {name for fields in _FIELD_GROUPS for name, _, _ in fields}

_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/@+-]{1,200}$")
_URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]{0,250}$")


def validate(name, value, kind=None):
    """An error message for `value`, or None. Blank always passes (it clears)."""
    if not value:
        return None
    kind = kind or ("provider" if name in ROUTING_FIELDS else None)
    if kind == "provider":
        from news_fetcher.llm_client import CHAT_PROVIDERS
        if name == "LLM_FAST_PROVIDER" and value == "main":
            return None
        return None if value in CHAT_PROVIDERS else "Unknown provider."
    if kind == "url":
        return None if _URL_RE.match(value) else "Enter a URL starting with http:// or https://."
    if kind == "model":
        return None if _MODEL_RE.match(value) else "Model names have no spaces and are at most 200 characters."
    if kind == "int":
        return None if value.isdigit() and 0 < int(value) <= 100000 else "Enter a whole number."
    if kind == "secret":
        if len(value) > 500 or any(c.isspace() for c in value):
            return "That doesn't look like an API key."
    return None


def saved_rows():
    """{name: ConfigSetting} for every saved setting."""
    return {row.key: row for row in ConfigSetting.query.filter(ConfigSetting.key.in_(ALL_NAMES)).all()}


def field_states(rows=None):
    """{name: state} for the page: where each setting's value comes from
    ("saved", "env" or "unset"), the saved non-secret value, and for a
    saved key its last four characters and whether it still decrypts.
    Never the key itself."""
    rows = saved_rows() if rows is None else rows
    states = {}
    for name in ALL_NAMES:
        row = rows.get(name)
        env = os.environ.get(name, "").strip()
        state = {"source": "saved" if row else ("env" if env else "unset"), "saved": "", "env": "",
                 "default": DEFAULTS.get(name, "")}
        if name in SECRET_NAMES:
            state["env_hint"] = env[-4:] if len(env) > 8 else ("set" if env else "")
            if row:
                state["hint"] = row.hint or ""
                state["unreadable"] = secret_box.decrypt(row.secret_value) is None
                state["updated_at"] = row.updated_at
                state["updated_by"] = row.updated_by
        else:
            state["env"] = env
            if row:
                state["saved"] = row.value or ""
        states[name] = state
    return states


def save(name, value, username):
    """Save one setting; a blank value removes it (back to the environment)."""
    if name not in ALL_NAMES:
        raise ValueError(f"Unknown setting {name!r}")
    row = ConfigSetting.query.get(name)
    if not value:
        if row:
            db.session.delete(row)
        return
    if row is None:
        row = ConfigSetting(key=name)
        db.session.add(row)
    if name in SECRET_NAMES:
        row.value = None
        row.secret_value = secret_box.encrypt(value)
        row.hint = value[-4:]
    else:
        row.value = value
        row.secret_value = None
        row.hint = None
    row.updated_at = datetime.utcnow()
    row.updated_by = username


def _read_overrides():
    """Saved settings as {name: plaintext}, read on its own connection so a
    failure (no table yet on an install that hasn't migrated) can't leave the
    request's session in a failed transaction."""
    with db.engine.connect() as conn:
        rows = conn.execute(text("SELECT key, value, secret_value FROM config_settings")).fetchall()
    overrides = {}
    for key, value, secret_value in rows:
        if key not in ALL_NAMES:
            continue
        if secret_value:
            plaintext = secret_box.decrypt(secret_value)
            if plaintext:
                overrides[key] = plaintext
        elif value:
            overrides[key] = value
    return overrides


_state = {"checked": 0.0, "fingerprint": None}


def config_source():
    """llm_client's config source: the saved overrides when they changed
    since the last call, None otherwise (or outside an app context)."""
    now = time.monotonic()
    if now - _state["checked"] < RELOAD_SECONDS or not has_app_context():
        return None
    _state["checked"] = now
    try:
        overrides = _read_overrides()
    except Exception as e:
        logger.debug("[llm_config] Saved settings unavailable: %s", e)
        overrides = {}
    fingerprint = tuple(sorted(overrides.items()))
    if fingerprint == _state["fingerprint"]:
        return None
    _state["fingerprint"] = fingerprint
    return overrides


def reload_now():
    """Apply the saved settings in this process right away (after a save)."""
    from news_fetcher import llm_client, saved_settings
    _state["checked"] = 0.0
    _state["fingerprint"] = None
    saved_settings.refresh()
    llm_client._sync_config()


def install():
    from news_fetcher import saved_settings
    _state["checked"] = 0.0
    _state["fingerprint"] = None
    saved_settings.set_source(config_source)
