# muckscraperHeadlinesGoogleNEW/news_fetcher/outlet_bias_llm.py
# news_fetcher/outlet_bias_llm.py

import os
import logging
from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context

from news_fetcher import llm_client

logger = logging.getLogger(__name__)

langfuse = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
    host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
)

BIAS_LABELS = {
    1: "Left",
    2: "Lean Left",
    3: "Center",
    4: "Lean Right",
    5: "Right"
}


@observe()
def _ask_ollama(prompt):
    """Send a prompt to the configured LLM provider and return the raw response string or None."""
    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"provider": llm_client.LLM_PROVIDER}
    )
    result = llm_client.generate_text(prompt, timeout=30)
    langfuse_context.update_current_observation(output=result)
    return result


def _parse_bias_score(raw, label):
    """Parse a 1-5 integer from Ollama's response."""
    if raw is None:
        return None
    if raw.lower() == "unknown":
        logger.info(f"  Ollama could not determine bias for: {label}")
        return None
    try:
        score = int(raw)
        if 1 <= score <= 5:
            return score
        return None
    except ValueError:
        logger.info(f"  Could not parse Ollama response for '{label}': {raw}")
        return None


@observe()
def get_outlet_bias_from_llm(outlet_name):
    """
    Ask Ollama to rate the political bias of a news outlet by name.
    Returns an integer 1-5 or None if it can't determine.
    """
    prompt = f"""You are a media bias analyst. Rate the political bias of the news outlet "{outlet_name}" on this scale:
1 = Left
2 = Lean Left
3 = Center
4 = Lean Right
5 = Right

Rules:
- Respond with a single integer between 1 and 5 only
- No explanation, no punctuation, just the number
- If you have never heard of the outlet or genuinely cannot determine its bias, respond with the single word: unknown

Outlet: {outlet_name}
Rating:"""

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"provider": llm_client.LLM_PROVIDER}
    )
    raw = _ask_ollama(prompt)
    langfuse_context.update_current_observation(
        output=raw
    )
    logger.info(f"  Ollama rated outlet '{outlet_name}': {raw}")
    return _parse_bias_score(raw, outlet_name)


@observe()
def get_article_bias_from_llm(title, content=None):
    """
    Ask Ollama to rate the political bias of a specific article
    based on its title and content snippet.
    Returns an integer 1-5 or None if it can't determine.
    """
    article_text = f"Title: {title}"
    if content:
        snippet = content[:600].strip()
        article_text += f"\n\nContent snippet: {snippet}"

    prompt = f"""You are a media bias analyst. Read the following news article and rate its political bias on this scale:
1 = Left
2 = Lean Left
3 = Center
4 = Lean Right
5 = Right

Consider the language used, framing, and perspective presented in the article itself.

Rules:
- Respond with a single integer between 1 and 5 only
- No explanation, no punctuation, just the number
- If you genuinely cannot determine the bias from the content, respond with the single word: unknown

Article:
{article_text}

Rating:"""

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"provider": llm_client.LLM_PROVIDER}
    )
    raw = _ask_ollama(prompt)
    langfuse_context.update_current_observation(
        output=raw
    )
    logger.info(f"  Ollama rated article '{title[:60]}...': {raw}")
    return _parse_bias_score(raw, title)
