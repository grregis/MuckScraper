# muckscraperHeadlinesGoogleNEW/news_fetcher/topic_classifier.py
# news_fetcher/topic_classifier.py

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

VALID_TOPICS = [
    "US Politics",
    "US News",
    "International News",
    "Sci/Tech",
    "Sports",
    "Buss/Fin",
    "Other",
]


@observe()
def classify_article(title, content_snippet=""):
    """
    Ask Ollama to classify an article into one or more topics.
    Returns a list of topic label strings.
    Falls back to ["Other"] if Ollama is unavailable or classification fails.
    """
    if not llm_client.is_configured():
        return ["Other"]

    # Use title + first 200 chars of content for classification
    text = title
    if content_snippet:
        clean = content_snippet[:200].strip()
        if clean:
            text += f"\n{clean}"

    topics_list = "\n".join(f"- {t}" for t in VALID_TOPICS if t != "Other")

    prompt = f"""You are a news editor categorizing articles. You must respond with ONLY category names from the list below, one per line. No other text, no notes, no explanations, no parentheses.

Article: "{text}"

Categories (choose only from these exact names):
- US Politics
- US News
- International News
- Sci/Tech
- Sports
- Buss/Fin
- Other

Rules:
- Use EXACT category names only — do not create new categories
- US Politics means US federal government, Congress, White House, elections, federal courts/policy, or any US government action or statement toward another country (diplomacy, sanctions, tariffs, military orders)
- International News means events, governments, conflicts, or disasters in other countries. If a story is about a US government action toward another country, use BOTH US Politics and International News
- US News means domestic US news that is NOT about government or politics — crime, accidents, disasters, lawsuits, local/state news, transportation, weather
- Entertainment, celebrity, lifestyle, and human-interest stories belong to Other, not US News
- Sci/Tech means technology, science, research, AI, space — NOT general business news about tech companies (use Buss/Fin for stock/earnings stories)
- Buss/Fin means financial markets, economics, corporate earnings, mergers — NOT general commerce
- Sports contracts and player signings belong to Sports only, not Buss/Fin
- Pick the most specific category — if it's clearly Sports, do not also add other categories
- Maximum 2 categories per article unless truly necessary
- If none apply, respond with only: Other
- Your entire response must be category names only — no parentheses, no notes, no commentary"""

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
        for valid in VALID_TOPICS:
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
