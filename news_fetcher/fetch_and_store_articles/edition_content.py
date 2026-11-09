# news_fetcher/fetch_and_store_articles/edition_content.py
"""
Filling a published edition with summaries, deep reports and child
article summaries (process_current_edition), plus the deep-report
backfill helper.
"""

import logging
import os
import time
from datetime import datetime, timedelta
import json
from aggregator import db
from aggregator.models import Article, Story
from news_fetcher import llm_client as _llm
from news_fetcher.summarizer import check_ollama_status

logger = logging.getLogger(__name__)


def generate_missing_deep_reports(batch_size=5):
    """Find multi-article stories picked for headlines that don't have deep reports."""
    if not check_ollama_status(_llm.TIER_QUALITY):
        logger.info("Quality-tier LLM offline, skipping deep report generation.")
        return

    from sqlalchemy import func
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=2)

    # Only target stories with a headline_score > 0 (meaning they were picked by the ranker)
    undissected = Story.query.join(Article).group_by(Story.id).having(
        func.count(Article.id) >= 2
    ).filter(
        Story.headline_score > 0,
        Story.created_at >= cutoff,
        (Story.deep_report == None) | (Story.deep_report == "")
    ).order_by(Story.headline_score.desc()).limit(batch_size).all()

    if not undissected:
        logger.info("No headline stories need deep reports.")
        return

    logger.info(f"Generating deep reports for {len(undissected)} headline stories...")
    from news_fetcher.summarizer import generate_deep_report
    for story in undissected:
        report = generate_deep_report(story)
        if report:
            story.deep_report = report
            logger.info(f"  Generated deep report for: {story.title[:60]}")
    
    db.session.commit()
    logger.info("Finished deep report batch.")


STALE_ARTICLE_THRESHOLD = 3  # new articles needed to trigger reanalysis

# How far back to look for stories left incomplete in a superseded edition
# (e.g. Ollama was unreachable while that edition was still "latest").
BACKFILL_LOOKBACK_DAYS = 2

# AppSetting key for the opt-in "also generate Article.deep_analysis during
# story fill" toggle. Stored via the same JSON-in-AppSetting mechanism the admin
# blueprint uses for other persisted settings.
AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY = "auto_article_deep_analysis_enabled"


def _auto_article_deep_analysis_enabled():
    """Whether ingestion should also generate per-article deep analysis.

    Off by default: deep-analysis prompts run at a higher timeout than article
    summaries and cost real extra GPU time for every qualifying article, so this
    should not silently turn on for everyone. Reads the AppSetting toggle
    directly (same pattern scraper.py uses for its retry cache) so the fetcher
    has no import dependency on the admin blueprint.
    """
    from aggregator.models import AppSetting

    setting = AppSetting.query.filter_by(
        key=AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY
    ).first()
    if not setting:
        return False
    try:
        return bool(json.loads(setting.value))
    except (ValueError, TypeError):
        return False


def _fill_story_content(story, metrics, analyse_articles=None, stale_threshold=STALE_ARTICLE_THRESHOLD):
    """
    Generate any missing summary/deep-report/child-article-summary content
    for a single story. Returns True if anything was generated or reset.

    analyse_articles: also write each article's deep analysis. None (edition
    stories) follows the Auto article deep analysis toggle; the analyse-all
    step passes True, since analysing everything is what it was turned on for.
    stale_threshold: new articles since the last story summary that make the
    summary and deep report be rewritten; the analyse-all step passes 1.
    """
    from news_fetcher.summarizer import (
        summarize_story,
        generate_deep_report,
        summarize_article,
        generate_article_deep_analysis,
        article_has_analysable_content,
    )

    article_count = len(story.articles)
    if article_count == 0:
        return False

    changed = False

    try:
        story_is_stale = False

        # Check if analysis is stale — enough new articles arrived
        # since the last time this story was summarized
        if story.summary_generated_at and article_count >= 2:
            new_article_count = sum(
                1 for a in story.articles
                if a.fetched_at and a.fetched_at > story.summary_generated_at
            )
            if new_article_count >= stale_threshold:
                logger.info(
                    f"  [Processor] Story stale ({new_article_count} new articles "
                    f"since last analysis): {story.title[:60]}"
                )
                # The old summary and deep report stay until their
                # replacements are written. Clearing them first meant a
                # failed rewrite (LLM down) saved blanks: 48 stories lost
                # their text in the 2026-11-08 22:00 ET outage.
                story_is_stale = True
                metrics["stale_stories_reset"] += 1

        if article_count >= 2:
            story_outputs_ready = bool(story.summary) and bool(story.deep_report)
        else:
            story_outputs_ready = bool(story.summary)

        missing_child_summaries = any(
            article.content and not article.summary
            for article in story.articles
        )
        missing_child_analyses = bool(analyse_articles) and any(
            not article.deep_analysis and article_has_analysable_content(article)
            for article in story.articles
        )

        if (not story_is_stale and story_outputs_ready and not missing_child_summaries
                and not missing_child_analyses):
            metrics["stable_stories_skipped"] += 1
            return changed

        # Article summaries and analyses first: the deep report reads them for
        # long articles (summarizer.article_digest). This is also critical for
        # the static site links.
        for article in story.articles:
            if not article.summary and article.content:
                summary = summarize_article(article)
                if summary:
                    article.summary = summary
                    metrics["child_article_summaries_generated"] += 1
                    changed = True
                    logger.info(f"    [Processor] Child article summary: {article.title[:60]}")
        db.session.commit()

        # Per-article deep analysis for every article in an edition story (this
        # function only runs for edition stories). Any topic qualifies; the
        # only filter is enough real article text to analyse. Off by default
        # for new installs -- see _auto_article_deep_analysis_enabled(). Measured
        # 2026-10-07 on gemma4-12b: ~3.7 s per article.
        if analyse_articles is None:
            analyse_articles = _auto_article_deep_analysis_enabled()
        if analyse_articles:
            for article in story.articles:
                if (
                    not article.deep_analysis
                    and article_has_analysable_content(article)
                ):
                    analysis = generate_article_deep_analysis(article)
                    if analysis:
                        article.deep_analysis = analysis
                        metrics["child_article_analyses_generated"] += 1
                        changed = True
                        logger.info(f"    [Processor] Child article deep analysis: {article.title[:60]}")
            db.session.commit()

        # 1. Process Story-level Summaries
        if article_count >= 2:
            summary = report = None
            if story_is_stale or not story.summary:
                summary = summarize_story(story)
                if summary:
                    story.summary = summary
                    metrics["story_summaries_generated"] += 1
                    changed = True
                    logger.info(f"  [Processor] Story summary: {story.title[:60]}")

            if story_is_stale or not story.deep_report:
                report = generate_deep_report(story)
                if report:
                    story.deep_report = report
                    metrics["deep_reports_generated"] += 1
                    changed = True
                    logger.info(f"  [Processor] Deep report: {story.title[:60]}")

            # summary_generated_at is what marks the story fresh, so a stale
            # story only gets it when BOTH rewrites landed; otherwise the next
            # run retries (a fresh summary beside a deep report that missed
            # the new articles would never be redone).
            if story_is_stale:
                if summary and report:
                    story.summary_generated_at = datetime.utcnow()
                else:
                    logger.info(
                        f"  [Processor] Kept previous story text (rewrite failed; "
                        f"retrying next run): {story.title[:60]}"
                    )
            elif summary:
                story.summary_generated_at = datetime.utcnow()
        else:
            # Single-article story: Ensure story summary exists
            story.headline = None
            if not story.summary:
                art = story.articles[0]
                summary = art.summary or summarize_article(art)
                if summary:
                    art.summary = summary
                    story.summary = summary
                    story.summary_generated_at = datetime.utcnow()
                    metrics["story_summaries_generated"] += 1
                    changed = True
                    logger.info(f"  [Processor] Single-source summary: {story.title[:60]}")

            # Ensure old stories that once had multiple articles (and thus a deep_report)
            # are cleaned up when they later appear as single-article stories.
            story.deep_report = None

        db.session.commit()

    except Exception as e:
        logger.error(f"  [Processor] Error processing story {story.id}: {e}")
        db.session.rollback()

    return changed


def process_current_edition(backfill_recent=False):
    """
    Exhaustively summarize and analyze the stories selected for the latest
    edition.
    1. Finds the most recent Edition and fills any missing summary/deep-report/
       child-article-summary content for its stories.
    2. If backfill_recent is True, also does the same, within
       BACKFILL_LOOKBACK_DAYS, for any other published edition's stories that
       are still incomplete — this catches stories that were left without a
       summary because Ollama was unreachable while their edition was still
       "latest" (it only recovered after a newer edition superseded it, so
       they'd otherwise never be revisited). Off by default since it touches
       editions beyond the current one — callers that want it (the scheduler)
       opt in explicitly.
    This ensures the static headlines site is fully populated.
    """
    from news_fetcher.summarizer import check_ollama_status
    from aggregator.models import Edition, EditionStory

    latest_edition = Edition.query.order_by(Edition.created_at.desc()).first()
    if not latest_edition:
        logger.info("[Processor] No edition found to process.")
        return {
            "status": "skipped_no_edition",
            "stories_seen": 0,
            "story_summaries_generated": 0,
            "deep_reports_generated": 0,
            "child_article_summaries_generated": 0,
            "child_article_analyses_generated": 0,
            "stale_stories_reset": 0,
            "stable_stories_skipped": 0,
            "backfilled_edition_ids": [],
        }

    # Summaries are the work this gates, so it asks about the quality tier.
    ollama_available = check_ollama_status(_llm.TIER_QUALITY)
    if not ollama_available:
        logger.warning(
            "[Processor] Ollama unreachable at start of edition processing; "
            "will still scan stories and skip generation calls until Ollama recovers."
        )

    stories = [es.story for es in latest_edition.edition_stories.order_by(EditionStory.rank).all()]
    metrics = {
        "status": "processed",
        "edition_id": latest_edition.id,
        "ollama_available_at_start": ollama_available,
        "stories_seen": len(stories),
        "story_summaries_generated": 0,
        "deep_reports_generated": 0,
        "child_article_summaries_generated": 0,
        "child_article_analyses_generated": 0,
        "stale_stories_reset": 0,
        "stable_stories_skipped": 0,
        "backfilled_edition_ids": [],
    }

    logger.info(f"[Processor] Processing {len(stories)} stories from {latest_edition.edition_type} edition...")

    processed_story_ids = set()
    for story in stories:
        processed_story_ids.add(story.id)
        _fill_story_content(story, metrics)

    if backfill_recent:
        cutoff = datetime.utcnow().date() - timedelta(days=BACKFILL_LOOKBACK_DAYS)
        stale_editions = Edition.query.filter(
            Edition.published == True,
            Edition.id != latest_edition.id,
            Edition.date >= cutoff,
        ).order_by(Edition.date.desc(), Edition.created_at.desc()).all()

        for edition in stale_editions:
            edition_changed = False
            for es in edition.edition_stories.order_by(EditionStory.rank).all():
                story = es.story
                if not story or story.id in processed_story_ids:
                    continue
                processed_story_ids.add(story.id)
                if _fill_story_content(story, metrics):
                    edition_changed = True
            if edition_changed:
                logger.info(
                    f"  [Processor] Backfilled stories in superseded edition "
                    f"{edition.date} {edition.edition_type}"
                )
                metrics["backfilled_edition_ids"].append(edition.id)

    logger.info("[Processor] Current edition processing complete.")
    return metrics


# ── Analyse every story, every run (opt-in) ──
# Off by default: editions only need their own stories filled, and doing every
# story roughly doubles a full run and quadruples a fetch-only one (measured
# 2026-11-04 on gemma4-12b via llama.cpp: about +30-40 min a run for ~270 new
# articles). ANALYZE_ALL_STORIES in .env sets the default; the switch on
# Settings > LLM providers overrides it until it is reset.
ANALYZE_ALL_STORIES_SETTING_KEY = "analyze_all_stories_enabled"
# Stories with an article fetched this recently are candidates. Longer than the
# gap between runs, so a story whose calls failed is retried next run.
ANALYZE_ALL_LOOKBACK_HOURS = 24
_TRUE_WORDS = {"1", "true", "yes", "on"}


def _env_flag(name, default=False):
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in _TRUE_WORDS


def analyze_all_stories_setting():
    """(enabled, source): source is "admin" when the Settings switch has been
    used, else "env" (ANALYZE_ALL_STORIES, default off)."""
    from aggregator.models import AppSetting

    setting = AppSetting.query.filter_by(key=ANALYZE_ALL_STORIES_SETTING_KEY).first()
    if setting is not None:
        try:
            return bool(json.loads(setting.value)), "admin"
        except (ValueError, TypeError):
            pass
    return _env_flag("ANALYZE_ALL_STORIES"), "env"


def analyze_all_stories_enabled():
    return analyze_all_stories_setting()[0]


def _analyze_all_max_seconds():
    try:
        minutes = float(os.environ.get("ANALYZE_ALL_MAX_MINUTES", "") or 90)
    except ValueError:
        minutes = 90
    return max(minutes, 1) * 60


def stories_to_analyze(since):
    """Stories with an article fetched since `since`, biggest first, so a run
    cut short by the time limit has done the stories with most coverage."""
    from sqlalchemy import func

    rows = (
        db.session.query(Story.id)
        .join(Article, Article.story_id == Story.id)
        .group_by(Story.id)
        .having(func.max(Article.fetched_at) >= since)
        .order_by(func.count(Article.id).desc(), func.max(Article.fetched_at).desc())
        .all()
    )
    return [story_id for (story_id,) in rows]


def analyze_all_recent_stories(lookback_hours=ANALYZE_ALL_LOOKBACK_HOURS):
    """Fill summaries, article analyses and deep reports for every story that
    gained an article in the last `lookback_hours`, not only edition stories.
    Runs only when analyze_all_stories_enabled(). Idempotent: a complete story
    costs one check; one that gained even one article since its last summary
    gets the new articles summarised and analysed and its own summary and deep
    report rewritten (editions wait for STALE_ARTICLE_THRESHOLD new articles)."""
    enabled, source = analyze_all_stories_setting()
    if not enabled:
        return {"status": "skipped", "reason": "off", "source": source}
    if not check_ollama_status(_llm.TIER_QUALITY):
        logger.info("[AnalyzeAll] Quality-tier LLM offline, skipping.")
        return {"status": "skipped", "reason": "llm_offline", "source": source}

    started = time.monotonic()
    limit = _analyze_all_max_seconds()
    story_ids = stories_to_analyze(datetime.utcnow() - timedelta(hours=lookback_hours))
    metrics = {
        "status": "ok",
        "source": source,
        "candidates": len(story_ids),
        "stories_seen": 0,
        "stories_changed": 0,
        "story_summaries_generated": 0,
        "deep_reports_generated": 0,
        "child_article_summaries_generated": 0,
        "child_article_analyses_generated": 0,
        "stale_stories_reset": 0,
        "stable_stories_skipped": 0,
    }
    logger.info(f"[AnalyzeAll] {len(story_ids)} stories with articles from the last {lookback_hours} h")
    for story_id in story_ids:
        if time.monotonic() - started > limit:
            metrics["status"] = "time_limit"
            logger.warning(
                f"[AnalyzeAll] Stopped at the {limit / 60:.0f}-minute limit "
                f"(ANALYZE_ALL_MAX_MINUTES) after {metrics['stories_seen']} of {len(story_ids)} stories; "
                "the rest are picked up next run."
            )
            break
        story = db.session.get(Story, story_id)
        if story is None:
            continue
        metrics["stories_seen"] += 1
        if _fill_story_content(story, metrics, analyse_articles=True, stale_threshold=1):
            metrics["stories_changed"] += 1
        db.session.expunge_all()  # keep the session small over thousands of stories
    metrics["duration_seconds"] = round(time.monotonic() - started, 1)
    logger.info(
        f"[AnalyzeAll] Done in {metrics['duration_seconds'] / 60:.1f} min: "
        f"{metrics['stories_changed']} stories changed, "
        f"{metrics['story_summaries_generated']} story summaries, "
        f"{metrics['deep_reports_generated']} deep reports, "
        f"{metrics['child_article_summaries_generated']} article summaries, "
        f"{metrics['child_article_analyses_generated']} article analyses"
    )
    return metrics
