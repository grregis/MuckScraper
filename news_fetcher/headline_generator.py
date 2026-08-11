# muckscraperHeadlinesGoogleNEW/news_fetcher/headline_generator.py
# news_fetcher/headline_generator.py

import logging
import os
from datetime import datetime, timedelta

from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context

from news_fetcher import llm_client
from news_fetcher.prompt_registry import render_prompt

logger = logging.getLogger(__name__)

# Headlines are the exception to the "mechanical calls go on the fast tier"
# rule: they are mechanical, but they are also the most reader-visible text on
# the site. On TIER_FAST the 8B model twice published invented figures --
# "1.7M ladders" became "Over 17 million" (2026-08-07), and "4 savings account
# options" became "$4 in interest" (2026-08-10, edition 281 rank 11). Prompt
# hardening fixed the padding but not the comprehension errors.
#
# This is only affordable because headlines are generated in ONE batch pass
# (generate_headlines_for_stale_stories, below) positioned immediately before
# the summary phase, which loads the same quality model anyway -- so the run
# pays zero extra model swaps. Ollama holds one model at a time; generating
# these inline per article on a different tier from grouping/classification
# would swap models on every story and cost roughly 40 minutes a run.
HEADLINE_TIER = llm_client.TIER_QUALITY

# How far back the per-run pass looks. Long enough to cover stories still
# accumulating articles, short enough that the pass does not walk the whole
# ~10k-story table every run.
STALE_HEADLINE_LOOKBACK_DAYS = 7

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
    sorted_articles = sorted(story.articles, key=lambda a: getattr(a, "date", None) or datetime.min, reverse=True)

    titles = "\n".join(
        f"- {article.title}" for article in sorted_articles[:10]
    )

    prompt = render_prompt("headline_generator", titles=titles)
    if prompt is None:
        logger.error("No prompt template found for 'headline_generator'")
        return None

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={
            "provider": llm_client.LLM_PROVIDER,
            "model": llm_client.model_for_tier(HEADLINE_TIER),
        }
    )
    # Timeout is generous because this runs on the quality model, which does not
    # fit entirely in VRAM -- a cold call measured 20.6s against 1.26s warm.
    headline = llm_client.generate_text(prompt, timeout=90, tier=HEADLINE_TIER)
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


def _stale_headline_query(lookback_days=None):
    """Multi-article stories whose headline is missing or out of date.

    Out of date means the story gained an article after its headline was
    written -- headline_generated_at older than the story's newest article.
    NULL headline_generated_at (every row before that column existed) counts as
    stale, so the first run after deploy refreshes them once.
    """
    from aggregator import db
    from aggregator.models import Article, Story

    per_story = (
        db.session.query(
            Article.story_id.label("story_id"),
            db.func.count(Article.id).label("article_count"),
            db.func.max(Article.fetched_at).label("newest_fetched_at"),
        )
        .filter(Article.story_id.isnot(None))
        .group_by(Article.story_id)
        .subquery()
    )

    query = (
        db.session.query(Story)
        .join(per_story, per_story.c.story_id == Story.id)
        .filter(per_story.c.article_count >= 2)
        .filter(
            db.or_(
                Story.headline.is_(None),
                db.func.length(db.func.trim(Story.headline)) == 0,
                Story.headline_generated_at.is_(None),
                Story.headline_generated_at < per_story.c.newest_fetched_at,
            )
        )
    )

    if lookback_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        query = query.filter(per_story.c.newest_fetched_at >= cutoff)

    return query.order_by(per_story.c.newest_fetched_at.desc())


def generate_headlines_for_stale_stories(lookback_days=STALE_HEADLINE_LOOKBACK_DAYS):
    """Generate every missing or stale headline in one pass.

    This is the only place headlines are written during a scheduled run.
    Position it after story membership has settled (after grouping review and
    any enrichment) and immediately before the summary phase, so the quality
    model loads once and is reused rather than swapped in and out.

    Idempotent: a story whose call fails keeps its old headline_generated_at
    and is simply picked up again on the next run.
    """
    from aggregator import db
    from news_fetcher.summarizer import check_ollama_status

    if not check_ollama_status():
        logger.info("LLM offline, skipping headline generation.")
        return {"status": "skipped", "reason": "llm_offline"}

    stories = _stale_headline_query(lookback_days).all()
    if not stories:
        logger.info("[Headlines] No missing or stale headlines.")
        return {"status": "ok", "candidates": 0, "generated": 0, "failed": 0}

    logger.info(f"[Headlines] Generating {len(stories)} missing/stale headlines...")
    generated = failed = 0
    for story in stories:
        headline = generate_story_headline(story)
        if headline:
            story.headline = headline
            story.headline_generated_at = datetime.utcnow()
            generated += 1
        else:
            failed += 1

    db.session.commit()
    logger.info(f"[Headlines] Generated {generated}, failed {failed}.")
    return {
        "status": "ok",
        "candidates": len(stories),
        "generated": generated,
        "failed": failed,
    }


def generate_missing_headlines():
    """Unscoped catch-up over the whole database, for manual repair.

    Kept separate from the per-run pass because it deliberately has no lookback
    window -- it will walk every story there is. Use the per-run pass for
    anything routine.
    """
    return generate_headlines_for_stale_stories(lookback_days=None)
