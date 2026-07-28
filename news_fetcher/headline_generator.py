# muckscraperHeadlinesGoogleNEW/news_fetcher/headline_generator.py
# news_fetcher/headline_generator.py

import logging
import os
from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context

from news_fetcher import llm_client

logger = logging.getLogger(__name__)

langfuse = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
    host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
)


@observe()
def generate_story_headline(story):
    """
    Generate a news wire style headline for a multi-article story.
    Returns a headline string or None if the LLM provider is unavailable.
    Only runs if the story has 2+ articles.
    """
    if not llm_client.is_configured():
        logger.warning("LLM provider not configured, skipping headline generation.")
        return None

    if len(story.articles) < 2:
        logger.debug(f"Story '{story.title}' has only 1 article, skipping headline.")
        return None

    # story.articles has no defined order — sort most-recent-first so a
    # story with more than 10 articles doesn't silently drop its newest
    # developments from the prompt just because they land later in the
    # collection's native DB order.
    from datetime import datetime
    sorted_articles = sorted(story.articles, key=lambda a: getattr(a, "date", None) or datetime.min, reverse=True)

    titles = "\n".join(
        f"- {article.title}" for article in sorted_articles[:10]
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
        metadata={"provider": llm_client.LLM_PROVIDER}
    )
    headline = llm_client.generate_text(prompt)
    if headline is None:
        logger.error(f"Error generating headline for '{story.title}'")
        return None

    langfuse_context.update_current_observation(output=headline)

    # Clean up common LLM artifacts
    headline = headline.strip('"\'').strip()

    if headline and len(headline.split()) <= 20:
        logger.info(f"Generated headline: '{headline}'")
        return headline

    logger.warning(f"Headline too long or empty: '{headline}'")
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
