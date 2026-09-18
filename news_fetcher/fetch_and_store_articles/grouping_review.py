# news_fetcher/fetch_and_store_articles/grouping_review.py
"""
Post-ingestion grouping correction: the ambiguous-match review pass, the
single-article regroup used by ollama_catchup, and the headline-clearing
rules that keep a stale Story.headline from hijacking later matches.
"""

import logging
from datetime import datetime, timedelta
from aggregator import db
from aggregator.models import Article, EditionStory, Story
from news_fetcher import llm_client as _llm
from news_fetcher.summarizer import check_ollama_status
from .ingestion import deserialize_grouping_candidate_ids, serialize_grouping_candidate_ids, truncate_db_string

logger = logging.getLogger(__name__)


def clear_story_headline_after_article_departs(story):
    """Clear a story's headline whenever it loses an article.

    Losing an article can only shrink the story's remaining newest-fetched-at,
    so the usual staleness check (headline_generated_at older than the newest
    article) never fires on a removal -- only clearing the headline outright
    forces `generate_headlines_for_stale_stories()` to rewrite it. Left
    unguarded on story size: a story with several articles left can still keep
    a headline that was actually about the one that just departed, and that
    stale headline is checked by `titles_are_near_duplicates()` against every
    future ambiguous article, so a wrong one can hijack matching outright
    before the LLM review ever runs. Confirmed live 2026-08-27: a story about
    Australian bird deaths kept the headline "Gang attack in Haiti leaves 47
    dead" after its one Haiti article was reassigned elsewhere, and that stale
    headline pulled an unrelated Haiti kidnapping article straight into the
    bird story on a title-token match, with no LLM call and no chance for the
    incumbent-labeling fix above to weigh in.
    """
    if story:
        story.headline = None


def clear_stale_single_article_headlines():
    """
    Remove stored AI headlines from stories that no longer qualify as
    multi-article stories.
    """
    stale_stories = (
        Story.query
        .outerjoin(Article, Story.id == Article.story_id)
        .group_by(Story.id)
        .having(db.func.count(Article.id) <= 1)
        .filter(
            Story.headline.isnot(None),
            db.func.length(db.func.trim(Story.headline)) > 0,
        )
        .all()
    )

    for story in stale_stories:
        story.headline = None

    if stale_stories:
        db.session.commit()
        logger.info(
            "[Headline Cleanup] Cleared stale headlines from %s single-article stories.",
            len(stale_stories),
        )

    return len(stale_stories)


def _reassign_edition_rows(from_story, to_story):
    """Move any EditionStory rows off `from_story` before it is deleted.

    Story.edition_stories carries delete-orphan cascade, so deleting a story
    that is still in a published edition silently removes that edition row
    -- the 2026-09-12 incident lost 11 ranks from a live edition exactly this
    way. Reassign each row to the surviving story instead, or drop it when
    that story is already in the same edition (the (edition, story) pair is
    unique). Callers flush afterwards.
    """
    for es in EditionStory.query.filter_by(story_id=from_story.id).all():
        dup = EditionStory.query.filter_by(
            edition_id=es.edition_id, story_id=to_story.id
        ).first()
        if dup:
            db.session.delete(es)
        else:
            es.story_id = to_story.id


def review_ambiguous_grouping_matches(max_articles=300):
    """
    Recheck articles that had an ambiguous first-pass grouping decision, oldest first.
    This is intentionally capped and only runs during the full pipeline.

    No age cutoff: an earlier version excluded articles older than 24h, which
    silently orphaned any backlog the cap couldn't clear in time (grouping_needs_review
    stayed True forever once an article aged out). Oldest-first ordering with no
    cutoff means the cap still bounds each run's Ollama load, but the backlog
    drains over successive runs instead of losing articles permanently.

    Bumped from 75 to 300 (2026-07-19) to drain the ~15k-article backlog this
    orphaned faster — each review is an Ollama call at ~10-15s, so this adds
    roughly 50-75 minutes per full-pipeline run. Turn back down if that starts
    crowding out the rest of the run.
    """
    from news_fetcher.story_grouper import find_matching_story_with_metadata

    # Fast tier: the review's only LLM call is ask_ollama_for_match().
    if not check_ollama_status(_llm.TIER_FAST):
        logger.info("Fast-tier LLM offline, skipping ambiguous grouping review.")
        return {"status": "skipped_no_ollama", "reviewed": 0, "reassigned": 0}

    articles = Article.query.filter(
        Article.grouping_needs_review == True,
        Article.grouping_reviewed_at.is_(None),
    ).order_by(Article.fetched_at.asc()).limit(max_articles).all()

    if not articles:
        logger.info("No ambiguous grouping matches need review.")
        return {"status": "ok", "reviewed": 0, "reassigned": 0}

    reviewed = 0
    reassigned = 0

    for article in articles:
        reviewed += 1
        candidate_ids = deserialize_grouping_candidate_ids(article.grouping_candidate_story_ids)
        candidate_stories = []
        if candidate_ids:
            candidate_stories = Story.query.filter(Story.id.in_(candidate_ids)).all()
        if article.story and all(story.id != article.story_id for story in candidate_stories):
            candidate_stories.append(article.story)

        if not candidate_stories:
            article.grouping_needs_review = False
            article.grouping_reviewed_at = datetime.utcnow()
            continue

        # exclude_article_id is essential here, not defensive: candidate_stories
        # includes the article's current story (appended above), so without it
        # the article matches its own embedding at 1.0 and this pass rubber-
        # stamps every existing placement instead of reviewing it.
        decision = find_matching_story_with_metadata(
            article.title,
            article.embedding,
            candidate_stories,
            article_content=article.content,
            exclude_article_id=article.id,
            incumbent_story=article.story,
        )

        original_story = article.story
        matched_story = decision.story
        if matched_story and matched_story.id != article.story_id:
            logger.info(
                "  [Grouping Review] Reassigning '%s' from '%s' to '%s'",
                article.title[:90],
                original_story.title[:90] if original_story else "no story",
                matched_story.title[:90],
            )
            # Move via the relationship, not the raw FK column. The matcher
            # above reads original_story.articles (candidate snippets, the
            # entity veto), so that collection is already loaded and cached
            # with this article in it. A raw column write leaves the cache
            # stale, and the "is it empty now?" check below then reads the
            # cache and never deletes the vacated story -- the same bug class
            # regroup_ungrouped_stories() had (2026-09-13), with a milder
            # symptom: empty stories accumulating instead of orphaned
            # articles. Assigning article.story updates both collections.
            article.story = matched_story
            db.session.flush()
            reassigned += 1

            for topic in list(original_story.topics) if original_story else []:
                if topic not in matched_story.topics:
                    matched_story.topics.append(topic)

            if original_story:
                clear_story_headline_after_article_departs(original_story)

            if original_story and not original_story.articles:
                _reassign_edition_rows(original_story, matched_story)
                db.session.flush()
                db.session.delete(original_story)

            # Headline regeneration is left to the batch pass, which runs after
            # this review step precisely so reassignments like this one have
            # already settled.

        article.grouping_match_method = truncate_db_string(decision.method, 32)
        article.grouping_confidence = decision.confidence
        article.grouping_candidate_story_ids = serialize_grouping_candidate_ids(decision.candidate_story_ids)
        article.grouping_needs_review = False
        article.grouping_reviewed_at = datetime.utcnow()

    db.session.commit()
    logger.info(
        "[Grouping Review] Reviewed %s ambiguous articles, reassigned %s.",
        reviewed,
        reassigned,
    )
    return {"status": "ok", "reviewed": reviewed, "reassigned": reassigned}


def regroup_ungrouped_stories():
    """
    Find single-article stories from the last 7 days and attempt
    to re-group them using the vector similarity matcher.
    """
    from news_fetcher.story_grouper import find_matching_story

    cutoff = datetime.utcnow() - timedelta(days=7)

    # Find stories that only have one article
    all_recent = Story.query.filter(Story.created_at >= cutoff).all()
    ungrouped_stories = [s for s in all_recent if len(s.articles) == 1]

    if not ungrouped_stories:
        logger.info("No single-article stories to re-group.")
        return

    logger.info(f"Checking {len(ungrouped_stories)} single-article stories for potential matches...")

    # Potential targets for merging (stories with > 1 article)
    multi_article_stories = [s for s in all_recent if len(s.articles) > 1]

    merged = 0
    for story in ungrouped_stories:
        if not story.articles:
            continue

        article = story.articles[0]
        if article.embedding is None:
            continue

        # Try to match to an existing multi-article story
        matched = find_matching_story(article.title, article.embedding, multi_article_stories, article_content=article.content)

        if matched and matched.id != story.id:
            logger.info(f"  [Re-group] Merging '{story.title}' into '{matched.title}'")

            # Move article to matched story via the relationship attribute, not
            # the raw article.story_id column. Story.articles was already
            # accessed above (len(s.articles) for every story in all_recent),
            # which loads and caches each story's articles collection. Setting
            # the FK column directly doesn't update that cached collection, so
            # story.articles still lists this article as a member; deleting
            # story below then has SQLAlchemy "disassociate" it by nulling the
            # FK right back to NULL (Story.articles has no delete/delete-orphan
            # cascade), silently orphaning the very article we just moved.
            # Assigning through article.story keeps both sides' in-memory
            # state correct so the later delete doesn't clobber it.
            article.story = matched
            db.session.flush()

            # Merge topic tags
            for topic in story.topics:
                if topic not in matched.topics:
                    matched.topics.append(topic)

            # If this singleton is currently sitting in a published edition,
            # move its EditionStory row to the surviving story first -- see
            # _reassign_edition_rows() for why deleting without this loses
            # the edition row.
            _reassign_edition_rows(story, matched)
            db.session.flush()

            # The merged story's headline is now stale, but regenerating it here
            # would swap the quality model in mid-merge-loop. ollama_catchup()
            # runs the batch headline pass after this function for that reason.

            # Delete the now-empty story
            db.session.delete(story)
            merged += 1

    db.session.commit()
    logger.info(f"Re-grouping complete. Merged {merged} stories.")
