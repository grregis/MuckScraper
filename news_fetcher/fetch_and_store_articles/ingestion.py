# news_fetcher/fetch_and_store_articles/ingestion.py
"""
Per-batch article storage: the ingestion blocklists, dedupe, outlet
creation, embedding, grouping and classification for one list of
normalized article dicts (store_articles), plus the metrics and
serialization helpers it and the review pass share.
"""

import logging
from datetime import datetime, timedelta
import json
import os
from aggregator import db
from aggregator.article_signals import bias_bucket_for_score, is_roundup_article, low_value_article_reason
from aggregator.models import Article, Outlet, Story, Topic, IngestionBlock
from news_fetcher.allsides_lookup import get_allsides_score
from news_fetcher.outlet_bias_llm import get_outlet_bias_from_llm
from news_fetcher.scraper import scrape_article
from news_fetcher.story_grouper import find_or_create_story, get_embedding
from news_fetcher.topic_classifier import classify_article
from .outlets import normalize_source_name, source_name_from_url

logger = logging.getLogger(__name__)


BLOCK_KIND_SOURCE = "source"
BLOCK_KIND_TITLE = "title_keyword"


def get_ingestion_blocks():
    """
    The ingestion blocklists -- DB-backed (IngestionBlock, admin-editable at
    /admin/ingestion-blocks) rather than two hardcoded lists.

    Returns (sources, title_keywords), both lowercased once here so the
    per-article checks don't re-lower on every comparison and an entry typed
    as "NFL.com" in the admin still matches. Loaded once per store_articles()
    batch, not per article: this is the hottest path in the fetch pipeline.
    """
    rows = IngestionBlock.query.filter_by(is_active=True).all()
    sources, title_keywords = [], []
    for row in rows:
        pattern = (row.pattern or "").strip().lower()
        if not pattern:
            continue
        if row.kind == BLOCK_KIND_SOURCE:
            sources.append(pattern)
        elif row.kind == BLOCK_KIND_TITLE:
            title_keywords.append(pattern)
    return sources, title_keywords


GROUPING_LOOKBACK_DAYS = int(os.getenv("MUCKSCRAPER_GROUPING_LOOKBACK_DAYS", "7"))


BIAS_BUCKETS = ("left", "lean_left", "center", "lean_right", "right", "unrated")


def empty_store_metrics(topic_name, provider=None, input_articles=0):
    return {
        "topic_name": topic_name,
        "provider": provider,
        "input_articles": input_articles,
        "stored": 0,
        "new_outlets": 0,
        "stories_touched": 0,
        "skipped": {
            "missing_required": 0,
            "blocked_source": 0,
            "blocked_title": 0,
            "low_value_url": 0,
            "roundup": 0,
            "betting": 0,
            "advice_column": 0,
            "duplicate_url": 0,
            "duplicate_title_outlet": 0,
        },
        "scrape_statuses": {},
        "bias_buckets": {bucket: 0 for bucket in BIAS_BUCKETS},
        "bias_sources": {
            "allsides": 0,
            "ai": 0,
            "unrated": 0,
        },
    }


def merge_count_maps(target, source):
    for key, value in (source or {}).items():
        target[key] = target.get(key, 0) + value


def serialize_grouping_candidate_ids(candidate_story_ids):
    ids = []
    for story_id in candidate_story_ids or []:
        try:
            ids.append(int(story_id))
        except (TypeError, ValueError):
            continue
    return json.dumps(ids) if ids else None


def deserialize_grouping_candidate_ids(raw_value):
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return []
    ids = []
    for story_id in parsed if isinstance(parsed, list) else []:
        try:
            ids.append(int(story_id))
        except (TypeError, ValueError):
            continue
    return ids


def truncate_db_string(value, max_length):
    if value is None:
        return None
    value = str(value)
    if len(value) <= max_length:
        return value
    return value[: max_length - 1] + "…"


def get_or_create_topic(topic_name):
    """Get existing topic or create a new one, handling race conditions."""
    topic = Topic.query.filter_by(name=topic_name).first()
    if not topic:
        try:
            topic = Topic(name=topic_name)
            db.session.add(topic)
            db.session.flush()
        except Exception:
            # Another process created it at the same time, roll back and fetch it
            db.session.rollback()
            topic = Topic.query.filter_by(name=topic_name).first()
    return topic


def normalize_url(url):
    """Strip query parameters from URL to detect duplicates."""
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        # Keep only scheme, netloc, and path
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    except Exception:
        return url


def detect_duplicate_outlet_content(content, outlet_id, exclude_article_id=None):
    """
    Check if scraped content is near-identical to other articles from the same outlet.
    This catches login/error pages that return the same HTML for every blocked request.
    Returns (is_duplicate: bool, reason: str or None).
    """
    if not content or not outlet_id:
        return False, None

    from news_fetcher.scraper import sanitize_html
    import re

    def strip_to_text(html, max_chars=2000):
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]

    clean_new = strip_to_text(content)
    if len(clean_new) < 100:
        return False, None

    from difflib import SequenceMatcher

    recent = Article.query.filter(
        Article.outlet_id == outlet_id,
        Article.content != None,
        Article.content != "",
    )
    if exclude_article_id:
        recent = recent.filter(Article.id != exclude_article_id)
    recent = recent.order_by(Article.id.desc()).limit(10).all()

    match_count = 0
    for article in recent:
        if not article.content:
            continue
        clean_existing = strip_to_text(article.content)
        if len(clean_existing) < 100:
            continue
        ratio = SequenceMatcher(None, clean_new, clean_existing).ratio()
        if ratio > 0.85:
            match_count += 1

    if match_count >= 2:
        reason = f"Bad scrape: content near-identical to {match_count} other articles from same outlet (login/error page)"
        return True, reason

    return False, None


def store_articles(articles_data, topic_name, provider=None):
    """
    Store a list of normalized article dicts into the database,
    tagging them with the given topic.
    articles_data: list of dicts with keys:
        title, content, url, source_name, published_at, image_url
    """
    metrics = empty_store_metrics(topic_name, provider=provider, input_articles=len(articles_data))
    stories_touched = set()

    # Pre-fetch recent stories once for the whole batch. This is the hottest
    # fetch path: every new article compares against this pool, so keep the
    # lookback configurable while we diagnose runtime trends.
    # No selectinload(Story.articles) here on purpose: the pgvector query in
    # find_matching_story_with_metadata() does the real embedding-match work
    # in SQL and never touches the ORM relationship, so eager-loading every
    # recent story's articles up front (~40s/run at ~3,440 stories) paid for
    # a collection almost nothing in the batch actually reads. What does read
    # story.articles -- the title_overlap_review/embedding_review snippet
    # lookups (a handful of candidates per call) and this loop's own
    # story.articles.append() below -- now lazy-loads per story touched,
    # which is bounded by batch size rather than the whole lookback pool.
    cutoff = datetime.utcnow() - timedelta(days=GROUPING_LOOKBACK_DAYS)
    recent_stories = (
        Story.query
        .filter(Story.created_at >= cutoff)
        .all()
    )
    logger.info(
        "  [Grouper] Loaded %s recent stories from the last %s day(s) for matching",
        len(recent_stories),
        GROUPING_LOOKBACK_DAYS,
    )

    # Same reasoning as the story pre-fetch above: every article in the batch
    # is tested against these, so load them once rather than per article.
    blocked_sources, blocked_title_keywords = get_ingestion_blocks()

    for article in articles_data:
        title        = article.get("title")
        content      = article.get("content") or ""
        raw_url      = article.get("url")
        source_name  = source_name_from_url(raw_url) or normalize_source_name(article.get("source_name", "Unknown"))
        published_at = article.get("published_at", datetime.utcnow())
        image_url    = article.get("image_url")

        if not title or not raw_url:
            metrics["skipped"]["missing_required"] += 1
            continue
            
        url = normalize_url(raw_url)

        if any(blocked in url.lower() for blocked in blocked_sources):
            logger.debug(f"Skipping blocked source: {url}")
            metrics["skipped"]["blocked_source"] += 1
            continue

        low_value_reason = low_value_article_reason(title, url)
        if low_value_reason:
            logger.debug(f"Skipping low-value article ({low_value_reason}): {title} [{url}]")
            metrics["skipped"][low_value_reason if low_value_reason in metrics["skipped"] else "low_value_url"] += 1
            continue

        if any(kw in title.lower() for kw in blocked_title_keywords):
            logger.debug(f"Skipping blocked title: {title}")
            metrics["skipped"]["blocked_title"] += 1
            continue

        # Check for URL duplicate (normalized)
        existing = Article.query.filter_by(url=url).first()
        if existing:
            logger.debug(f"Skipping duplicate URL: {title}")
            metrics["skipped"]["duplicate_url"] += 1
            continue

        # Check for Title + Source duplicate (catch same article, different URL)
        # First get/create outlet to have the ID
        outlet = Outlet.query.filter_by(name=source_name).first()
        if outlet:
            existing_title = Article.query.filter_by(title=title, outlet_id=outlet.id).first()
            if existing_title:
                logger.debug(f"Skipping duplicate Title+Outlet: {title}")
                metrics["skipped"]["duplicate_title_outlet"] += 1
                continue
        
        logger.info(f"Processing: {title}")

        if is_roundup_article(title, raw_url):
            image_url = None

        if not outlet:
            as_score = get_allsides_score(source_name)
            if as_score is not None:
                logger.info(f"  New outlet {source_name}: AllSides rating {as_score}")
                bias_score = as_score
                bias_source = "allsides"
                allsides_bias_score = as_score
            else:
                logger.info(f"  New outlet {source_name}: no AllSides rating, asking Ollama...")
                bias_score = get_outlet_bias_from_llm(source_name)
                bias_source = "ai" if bias_score is not None else None
                allsides_bias_score = None

            outlet = Outlet(
                name=source_name,
                url=url,
                description="N/A",
                bias_score=bias_score,
                allsides_bias_score=allsides_bias_score,
                bias_source=bias_source
            )
            db.session.add(outlet)
            db.session.flush()
            metrics["new_outlets"] += 1

        # Generate embedding for this article
        # Use title + snippet for better semantic matching
        from news_fetcher.story_grouper import strip_video_prefix
        clean_title = strip_video_prefix(title)
        embed_text = clean_title
        if content:
            from news_fetcher.summarizer import strip_html
            snippet = strip_html(content)[:200].strip()
            embed_text = f"{clean_title}. {snippet}"
        article_embedding = get_embedding(embed_text)
        
        story, match = find_or_create_story(title, db, Story, recent_stories,
                                            article_embedding=article_embedding,
                                            article_content=content)
        stories_touched.add(story.id)

        # Add new story to recent_stories so subsequent articles
        # in this same batch can match against it
        if story not in recent_stories:
            recent_stories.append(story)

        # Classify article into topics via Ollama
        from aggregator.models import Topic as TopicModel
        classified_topic_names = classify_article(title, content)
        for classified_name in classified_topic_names:
            classified_topic = TopicModel.query.filter_by(name=classified_name).first()
            if not classified_topic:
                classified_topic = TopicModel(name=classified_name)
                db.session.add(classified_topic)
                db.session.flush()
            if classified_topic not in story.topics:
                story.topics.append(classified_topic)

        scrape_result = scrape_article(url, fallback_content=content)
        scraped_content = scrape_result.content
        if scraped_content:
            # Check if this looks like a duplicate login/error page across the outlet
            is_dup, dup_reason = detect_duplicate_outlet_content(scraped_content, outlet.id)
            if is_dup:
                logger.warning(f"  [Scraper] {dup_reason} — clearing content and blocking domain for {url[:60]}")
                from news_fetcher.scraper import add_to_blocklist
                add_to_blocklist(url, dup_reason)
                scraped_content = None
                scrape_result.content = None
                scrape_result.status = "blocked"
                scrape_result.failure_reason = dup_reason

        if scraped_content:
            final_content = scraped_content
        else:
            from news_fetcher.scraper import sanitize_html
            final_content = sanitize_html(f"<div>{content}</div>") if content else ""

        # Ensure embedding is a list, not a string
        if isinstance(article_embedding, str):
            import json
            article_embedding = json.loads(article_embedding)

        new_article = Article(
            title=title,
            content=final_content,
            source=source_name,
            outlet_id=outlet.id,
            # story_id is deliberately NOT set here -- the append below establishes
            # it. Setting the FK here as well makes the collection's first lazy
            # load autoflush this row, so the load returns it AND the append adds
            # the same object again, double-counting len(story.articles).
            url=url,
            date=published_at,
            fetched_at=datetime.utcnow(),
            bias_score=outlet.bias_score,
            image_url=image_url,
            embedding=article_embedding,
            scrape_status=scrape_result.status,
            scrape_method=truncate_db_string(scrape_result.method, 255),
            scrape_failure_reason=truncate_db_string(scrape_result.failure_reason, 1024),
            scrape_http_status=scrape_result.http_status,
            grouping_match_method=truncate_db_string(getattr(match, "method", None), 32),
            grouping_confidence=getattr(match, "confidence", None),
            grouping_candidate_story_ids=serialize_grouping_candidate_ids(
                getattr(match, "candidate_story_ids", None)
            ),
            grouping_needs_review=bool(getattr(match, "needs_review", False)),
        )

        # IMPORTANT: append BEFORE session.add, and keep the FK out of the
        # constructor above. Appending is what makes the new article visible to
        # find_matching_story for subsequent articles in this SAME loop iteration;
        # doing it while the article is still transient means the collection's
        # first lazy load has nothing pending to flush, so the article lands in
        # story.articles exactly once and the >= 2 guard below is honest.
        story.articles.append(new_article)
        db.session.add(new_article)

        # Tag article with same topics as story
        for t in story.topics:
            if t not in new_article.topics:
                new_article.topics.append(t)

        # Headlines are no longer generated here. They run in one batch pass
        # (generate_headlines_for_stale_stories) after grouping settles, so the
        # quality model loads once per run instead of being swapped in and out
        # around every grouping/classification call. This story is now stale by
        # definition -- it just gained an article -- and the pass finds it via
        # headline_generated_at being older than the newest article.
        if len(story.articles) < 2:
            # For single-article stories, ensure story headline is cleared
            # so the UI falls back to story.title (original article title)
            story.headline = None
            story.headline_generated_at = None
                
        metrics["stored"] += 1
        metrics["scrape_statuses"][scrape_result.status] = (
            metrics["scrape_statuses"].get(scrape_result.status, 0) + 1
        )
        bias_bucket = bias_bucket_for_score(outlet.bias_score)
        metrics["bias_buckets"][bias_bucket] += 1
        bias_source = outlet.bias_source or "unrated"
        metrics["bias_sources"][bias_source] = metrics["bias_sources"].get(bias_source, 0) + 1

    db.session.commit()
    metrics["stories_touched"] = len(stories_touched)
    logger.info(
        "Stored %s new articles for topic: %s (provider=%s, skipped=%s)",
        metrics["stored"],
        topic_name,
        provider or "unknown",
        metrics["skipped"],
    )
    return metrics
