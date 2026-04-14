# muckscraperHeadlinesGoogleNEW/news_fetcher/headline_generator.py
# news_fetcher/headline_generator.py

import logging
import os
import requests
from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context

logger = logging.getLogger(__name__)

langfuse = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
    host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
)

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "")
MODEL       = os.environ.get("OLLAMA_MODEL", "")


@observe()
def generate_story_headline(story):
    """
    Generate a news wire style headline for a multi-article story.
    Returns a headline string or None if Ollama is unavailable.
    Only runs if the story has 2+ articles.
    """
    if not OLLAMA_HOST or not MODEL:
        logger.warning("Ollama not configured, skipping headline generation.")
        return None

    if len(story.articles) < 2:
        logger.debug(f"Story '{story.title}' has only 1 article, skipping headline.")
        return None

    titles = "\n".join(
        f"- {article.title}" for article in story.articles[:10]
    )

    prompt = f"""You are a wire service editor writing a single headline.

Below are multiple news articles covering the same story:
{titles}

Write ONE headline for this story in wire service style.

Rules:
- Who/what/where in one line
- Maximum 15 words
- Present tense, active voice
- No punctuation at the end
- No quotes around the headline
- Do not include source names or outlet names
- Respond with ONLY the headline, nothing else"""

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"model": MODEL}
    )
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model":  MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=30,
        )
        response.raise_for_status()

        headline = response.json().get("response", "").strip()
        langfuse_context.update_current_observation(
            output=headline
        )

        # Clean up common LLM artifacts
        headline = headline.strip('"\'').strip()

        if headline and len(headline.split()) <= 20:
            logger.info(f"Generated headline: '{headline}'")
            return headline

        logger.warning(f"Headline too long or empty: '{headline}'")
        return None

    except Exception as e:
        logger.error(f"Error generating headline for '{story.title}': {e}")
        return None


def generate_missing_headlines():
    """
    Find multi-article stories without headlines and generate them.
    Called during Ollama catchup.
    """
    from aggregator import db
    from aggregator.models import Story
    from news_fetcher.summarizer import check_ollama_status

    if not check_ollama_status():
        logger.info("Ollama offline, skipping headline generation.")
        return

    # Find multi-article stories without a headline
    stories = Story.query.all()
    missing = [s for s in stories if len(s.articles) >= 2 and not s.headline]

    if not missing:
        logger.info("All multi-article stories have headlines.")
        return

    logger.info(f"Generating headlines for {len(missing)} stories...")
    count = 0
    for story in missing:
        headline = generate_story_headline(story)
        if headline:
            story.headline = headline
            count += 1

    db.session.commit()
    logger.info(f"Generated {count} headlines.")
