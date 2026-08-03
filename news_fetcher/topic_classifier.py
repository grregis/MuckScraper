# muckscraperHeadlinesGoogleNEW/news_fetcher/topic_classifier.py
# news_fetcher/topic_classifier.py

import os
import time
import logging
from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context

from news_fetcher import llm_client
from news_fetcher.prompt_registry import render_prompt

logger = logging.getLogger(__name__)

langfuse = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
    host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
)

# Default taxonomy, used only if the DB is briefly unreachable (see
# _active_topic_names below) — keeps a transient DB hiccup from crashing
# classification of every remaining article in a batch.
_FALLBACK_TOPIC_NAMES = [
    "US Politics", "US News", "International News",
    "Sci/Tech", "Sports", "Buss/Fin", "Other",
]
_TOPIC_NAME_CACHE_TTL_SECONDS = 60
_topic_name_cache = {"names": None, "expires_at": 0.0}


def _active_topic_names():
    """
    Active topic names from the DB, in admin-configured order. Cached for
    _TOPIC_NAME_CACHE_TTL_SECONDS so a batch classification run (one call
    per article) doesn't re-query on every article. The rule bullets in
    the classification prompt below are written for the default
    7-category taxonomy — adding/removing topics via the admin UI changes
    which names the model can choose from, but the semantic guidance
    still reflects the default set until prompts are made configurable
    too (tracked as a follow-up).
    """
    now = time.monotonic()
    if _topic_name_cache["names"] is not None and now < _topic_name_cache["expires_at"]:
        return _topic_name_cache["names"]

    from aggregator import db
    from aggregator.models import Topic
    try:
        names = [t.name for t in Topic.query.filter_by(is_active=True).order_by(Topic.sort_order).all()]
    except Exception as e:
        db.session.rollback()
        logger.error(f"  [Classifier] Failed to load active topics from DB, using default taxonomy: {e}")
        return _FALLBACK_TOPIC_NAMES

    _topic_name_cache["names"] = names
    _topic_name_cache["expires_at"] = now + _TOPIC_NAME_CACHE_TTL_SECONDS
    return names


@observe()
def classify_article(title, content_snippet=""):
    """
    Ask Ollama to classify an article into one or more topics.
    Returns a list of topic label strings.
    Falls back to ["Other"] if Ollama is unavailable or classification fails.
    """
    if not llm_client.is_configured():
        return ["Other"]

    valid_topics = _active_topic_names()
    if not valid_topics:
        return ["Other"]

    # Use title + first 200 chars of content for classification
    text = title
    if content_snippet:
        clean = content_snippet[:200].strip()
        if clean:
            text += f"\n{clean}"

    categories_list = "\n".join(f"- {t}" for t in valid_topics if t != "Other")

    prompt = render_prompt("topic_classifier", text=text, categories_list=categories_list)
    if prompt is None:
        return ["Other"]

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"provider": llm_client.LLM_PROVIDER}
    )
    result = llm_client.generate_text(prompt, timeout=30)
    if result is None:
        logger.info("  [Classifier] No response, using Other")
        return ["Other"]

    langfuse_context.update_current_observation(output=result)

    lines  = [line.strip() for line in result.splitlines() if line.strip()]

    matched = []
    for line in lines:
        for valid in valid_topics:
            if valid.lower() in line.lower():
                if valid not in matched:
                    matched.append(valid)

    if matched:
        # Remove "Other" if any real categories were found
        matched = [t for t in matched if t != "Other"]
        if matched:
            logger.info(f"  [Classifier] Tagged as: {', '.join(matched)}")
            return matched

    logger.info(f"  [Classifier] No match found, using Other")
    return ["Other"]
