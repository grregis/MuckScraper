# news_fetcher/prompt_registry.py
#
# DB-backed LLM prompt templates, admin-editable via /admin/prompts. Each
# prompt is stored with an immutable `default_text` (the original hardcoded
# prompt, used for "reset to default") and a live `current_text` that
# render_prompt() renders against per-call variables via str.format().

import string
import time
import logging

logger = logging.getLogger(__name__)

_PROMPT_CACHE_TTL_SECONDS = 60
_prompt_cache = {}  # key -> (current_text, expires_at)

# The exact set of placeholder names each prompt key supports. Used both to
# build dummy values for save-time validation and to list what's valid in a
# validation error message.
KNOWN_VARS = {
    "story_summary": {"persona", "combined"},
    "deep_report.politics": {"source_availability", "combined"},
    "deep_report.science": {"combined"},
    "deep_report.sports": {"combined"},
    "deep_report.business": {"combined"},
    "deep_report.default": {"combined"},
    "article_summary": {"persona", "article_title", "clean_content"},
    "article_deep_analysis.politics": {"article_title", "clean_content"},
    "article_deep_analysis.science": {"article_title", "clean_content"},
    "article_deep_analysis.business": {"article_title", "clean_content"},
    "topic_classifier": {"text", "categories_list"},
    "outlet_bias.by_name": {"outlet_name"},
    "outlet_bias.by_article": {"article_text"},
    "headline_generator": {"titles"},
}


def _load_prompt_row(key):
    from aggregator.models import PromptTemplate
    return PromptTemplate.query.filter_by(key=key).first()


def get_current_text(key):
    """Cached lookup of a prompt's live text -- short TTL since this sits on
    the hot per-article/per-story LLM call path."""
    now = time.monotonic()
    cached = _prompt_cache.get(key)
    if cached and now < cached[1]:
        return cached[0]

    row = _load_prompt_row(key)
    text = row.current_text if row else None
    _prompt_cache[key] = (text, now + _PROMPT_CACHE_TTL_SECONDS)
    return text


def invalidate_cache(key=None):
    """Call after saving/resetting a prompt so the next render picks it up
    immediately instead of waiting out the TTL."""
    if key is None:
        _prompt_cache.clear()
    else:
        _prompt_cache.pop(key, None)


def render_prompt(key, **kwargs):
    """
    Render a stored prompt template against the given variables.

    Falls back to the row's default_text (and logs a warning) if the live
    text fails to format -- e.g. a direct DB edit bypassed save-time
    validation -- so a bad prompt can never crash a live pipeline run.
    Returns None if there's no template row at all, or if even the default
    fails to render (should not happen in practice).
    """
    text = get_current_text(key)
    if text is None:
        logger.error("  [PromptRegistry] No prompt template found for key '%s'", key)
        return None

    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError) as e:
        logger.warning(
            "  [PromptRegistry] Prompt '%s' failed to render (%s); falling back to default.",
            key, e,
        )
        row = _load_prompt_row(key)
        if row and row.default_text:
            try:
                return row.default_text.format(**kwargs)
            except (KeyError, IndexError, ValueError) as e2:
                logger.error(
                    "  [PromptRegistry] Default prompt for '%s' also failed to render: %s",
                    key, e2,
                )
        return None


def _extract_placeholders(text):
    """Names referenced by {name} (or {name.attr}/{name[0]}) in a
    format-style template string."""
    names = set()
    for _, field_name, _, _ in string.Formatter().parse(text):
        if field_name:
            names.add(field_name.split(".")[0].split("[")[0])
    return names


def validate_prompt_text(key, text):
    """
    Save-time check: try formatting with dummy values for this prompt's
    known variables.

    Returns (error, warning):
    - error: a string describing why the save should be rejected, or None
      if the text is safe to save.
    - warning: a non-blocking heads-up (e.g. a placeholder present in the
      default text is missing from this version), or None.
    """
    allowed = KNOWN_VARS.get(key, set())
    dummy = {name: f"<{name}>" for name in allowed}

    try:
        text.format(**dummy)
    except KeyError as e:
        bad_name = e.args[0]
        return (
            f"Unknown placeholder: {{{bad_name}}} — this prompt supports: "
            f"{', '.join(sorted(allowed)) or '(no placeholders)'}",
            None,
        )
    except (ValueError, IndexError) as e:
        return (f"Invalid formatting syntax in prompt: {e}", None)

    warning = None
    row = _load_prompt_row(key)
    if row:
        default_vars = _extract_placeholders(row.default_text)
        used_vars = _extract_placeholders(text)
        missing = sorted(default_vars - used_vars)
        if missing:
            placeholders = ", ".join("{" + m + "}" for m in missing)
            warning = (
                f"Heads up: the default prompt includes {placeholders}, "
                f"which this version doesn't use."
            )
    return (None, warning)
