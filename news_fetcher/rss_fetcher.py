# news_fetcher/rss_fetcher.py

import feedparser
import logging
from collections import Counter
from datetime import datetime
from datetime import timedelta
from aggregator.article_signals import bias_bucket_for_score
from news_fetcher.fetch_and_store_articles import merge_count_maps
from news_fetcher.story_grouper import normalize_title_tokens, titles_are_near_duplicates

logger = logging.getLogger(__name__)

RIGHT_ENRICHMENT_TOPIC = "Targeted Right RSS Enrichment"
LEFT_ENRICHMENT_TOPIC = "Targeted Left RSS Enrichment"


def _active_feed_urls(bucket):
    """Enabled RssFeed URLs for a bucket (general / right_enrichment / left_enrichment)."""
    from aggregator.models import RssFeed
    return [f.url for f in RssFeed.query.filter_by(bucket=bucket, enabled=True).all()]


def _parse_published(entry):
    """Convert feedparser's published_parsed struct_time to datetime."""
    try:
        if entry.get("published_parsed"):
            return datetime(*entry.published_parsed[:6])
        if entry.get("updated_parsed"):
            return datetime(*entry.updated_parsed[:6])
    except Exception:
        pass
    return datetime.utcnow()


def _extract_image(entry):
    """Try to pull an image URL from various RSS media fields."""
    media = entry.get("media_content", [])
    if media and isinstance(media, list):
        url = media[0].get("url")
        if url:
            return url
    enclosures = entry.get("enclosures", [])
    if enclosures:
        url = enclosures[0].get("url") or enclosures[0].get("href")
        if url:
            return url
    thumbnail = entry.get("media_thumbnail", [])
    if thumbnail and isinstance(thumbnail, list):
        url = thumbnail[0].get("url")
        if url:
            return url
    return None


def fetch_feed(feed_url, max_entries=30):
    """
    Fetch a single RSS feed.
    Returns (source_name, list of normalized article dicts).
    """
    try:
        feed = feedparser.parse(feed_url)
        source_name = (
            feed.feed.get("title", feed_url.split("/")[2])
            if feed.feed else feed_url.split("/")[2]
        )

        articles = []
        for entry in feed.entries[:max_entries]:
            title = entry.get("title", "").strip()
            url = entry.get("link", "").strip()
            if not title or not url:
                continue

            # Pass description as fallback content — scraper will attempt full text first
            content = entry.get("summary", "") or entry.get("description", "") or ""

            articles.append({
                "title":        title,
                "content":      content,
                "url":          url,
                "source_name":  source_name,
                "published_at": _parse_published(entry),
                "image_url":    _extract_image(entry),
            })

        logger.info(f"  [RSS] Got {len(articles)} articles from {feed_url[:60]}")
        return source_name, articles

    except Exception as e:
        logger.warning(f"  [RSS] Failed to fetch {feed_url[:60]}: {e}")
        return None, []


RSS_MAX_ENTRIES_PER_FEED_GROQ = 10


def fetch_and_store_rss():
    """
    Fetch all RSS feeds and store articles via the normal ingestion pipeline.
    Each article goes through the same dedup, scraping, embedding, topic
    classification, and story grouping as NewsAPI/GNews articles.
    Must be called within a Flask app context.

    While Groq serves the fast tier, entries per feed are capped well below the
    normal 30 — every stored article costs at least one classify_article()
    call, and Groq's free-tier TPM budget can't keep up with the full
    wire-service volume.

    Keyed on the *fast* provider, not the global one: classification is a
    fast-tier call, so under split routing (LLM_PROVIDER=openrouter +
    LLM_FAST_PROVIDER=groq) the global provider says nothing about whether
    this budget applies.
    """
    from news_fetcher.fetch_and_store_articles import store_articles
    from news_fetcher import llm_client

    fast_provider = llm_client.provider_for_tier(llm_client.TIER_FAST)
    max_entries = RSS_MAX_ENTRIES_PER_FEED_GROQ if fast_provider == "groq" else 30
    feed_urls = _active_feed_urls("general")

    logger.info("=== RSS fetch starting ===")
    total = 0
    metrics = {
        "provider": "rss",
        "status": "ok",
        "feeds_attempted": len(feed_urls),
        "feeds_with_articles": 0,
        "input_articles": 0,
        "stored": 0,
        "new_outlets": 0,
        "stories_touched": 0,
        "skipped": {},
        "scrape_statuses": {},
        "bias_buckets": {},
        "bias_sources": {},
        "per_feed": [],
    }

    for feed_url in feed_urls:
        source_name, articles = fetch_feed(feed_url, max_entries=max_entries)
        if articles:
            feed_metrics = store_articles(articles, "Global News", provider="rss")
            metrics["feeds_with_articles"] += 1
            metrics["input_articles"] += feed_metrics.get("input_articles", 0)
            metrics["stored"] += feed_metrics.get("stored", 0)
            metrics["new_outlets"] += feed_metrics.get("new_outlets", 0)
            metrics["stories_touched"] += feed_metrics.get("stories_touched", 0)
            merge_count_maps(metrics["skipped"], feed_metrics.get("skipped"))
            merge_count_maps(metrics["scrape_statuses"], feed_metrics.get("scrape_statuses"))
            merge_count_maps(metrics["bias_buckets"], feed_metrics.get("bias_buckets"))
            merge_count_maps(metrics["bias_sources"], feed_metrics.get("bias_sources"))
            metrics["per_feed"].append({
                "feed_url": feed_url,
                "source_name": source_name,
                "input_articles": feed_metrics.get("input_articles", 0),
                "stored": feed_metrics.get("stored", 0),
            })
            total += len(articles)
        else:
            metrics["per_feed"].append({
                "feed_url": feed_url,
                "source_name": source_name,
                "input_articles": 0,
                "stored": 0,
            })

    logger.info(f"=== RSS fetch complete. Processed {total} articles. ===")
    return metrics


def _story_bias_counts(story):
    counts = Counter()
    for article in story.articles:
        score = article.bias_score
        if score is None and article.outlet:
            score = article.outlet.bias_score
        counts[bias_bucket_for_score(score)] += 1
    return counts


def _story_reference_titles(story, max_titles=6):
    titles = []
    headline = getattr(story, "display_headline", None) or getattr(story, "headline", None)
    if headline:
        titles.append(headline)
    if story.title and story.title not in titles:
        titles.append(story.title)
    for article in story.articles[:max_titles]:
        if article.title and article.title not in titles:
            titles.append(article.title)
    return titles


def _story_keyword_tokens(story, max_tokens=8):
    token_counts = Counter()
    for title in _story_reference_titles(story):
        for token in normalize_title_tokens(title):
            token_counts[token] += 1
    return {token for token, _ in token_counts.most_common(max_tokens)}


def _story_needs_right_enrichment(story):
    if len(story.articles) < 2:
        return False

    counts = _story_bias_counts(story)
    if counts["lean_right"] or counts["right"]:
        return False

    rated_total = sum(counts[bucket] for bucket in ("left", "lean_left", "center", "lean_right", "right"))
    if rated_total < 2:
        return False

    leftish_total = counts["left"] + counts["lean_left"]
    if leftish_total >= 2:
        return True

    return leftish_total >= 1 and counts["center"] >= 2


def _story_needs_left_enrichment(story):
    if len(story.articles) < 2:
        return False

    counts = _story_bias_counts(story)
    if counts["left"] or counts["lean_left"]:
        return False

    rated_total = sum(counts[bucket] for bucket in ("left", "lean_left", "center", "lean_right", "right"))
    if rated_total < 2:
        return False

    rightish_total = counts["right"] + counts["lean_right"]
    if rightish_total >= 2:
        return True

    return rightish_total >= 1 and counts["center"] >= 2


def get_skewed_story_ids_for_right_enrichment(max_stories=5, candidate_pool=30, story_age_days=3):
    """
    Select top-ranked story IDs that still look skewed away from right coverage.
    Intended for a post-rerank, targeted second enrichment pass.
    """
    from aggregator.models import Story

    story_cutoff = datetime.utcnow() - timedelta(days=story_age_days)
    story_candidates = (
        Story.query
        .filter(
            Story.headline_score > 0,
            Story.created_at >= story_cutoff,
        )
        .order_by(Story.headline_score.desc())
        .limit(candidate_pool)
        .all()
    )

    selected = []
    for story in story_candidates:
        if not _story_needs_right_enrichment(story):
            continue
        selected.append(story.id)
        if len(selected) >= max_stories:
            break
    return selected


def get_skewed_story_ids_for_left_enrichment(max_stories=5, candidate_pool=30, story_age_days=3):
    """
    Select top-ranked story IDs that still look skewed away from left coverage.
    Intended for a post-rerank, targeted second enrichment pass.
    """
    from aggregator.models import Story

    story_cutoff = datetime.utcnow() - timedelta(days=story_age_days)
    story_candidates = (
        Story.query
        .filter(
            Story.headline_score > 0,
            Story.created_at >= story_cutoff,
        )
        .order_by(Story.headline_score.desc())
        .limit(candidate_pool)
        .all()
    )

    selected = []
    for story in story_candidates:
        if not _story_needs_left_enrichment(story):
            continue
        selected.append(story.id)
        if len(selected) >= max_stories:
            break
    return selected


def _feed_article_matches_story(article_data, story):
    article_title = (article_data.get("title") or "").strip()
    if not article_title:
        return False

    article_tokens = normalize_title_tokens(article_title)
    if not article_tokens:
        return False

    story_tokens = _story_keyword_tokens(story)
    if len(article_tokens & story_tokens) >= 3:
        return True

    for reference_title in _story_reference_titles(story):
        if titles_are_near_duplicates(article_title, reference_title):
            return True
        if len(article_tokens & normalize_title_tokens(reference_title)) >= 3:
            return True

    return False


def _enrich_stories_with_feed_set(
    target_stories,
    feed_urls,
    provider,
    topic_name,
    max_articles_per_story=3,
    lookback_hours=72,
):
    from news_fetcher.fetch_and_store_articles import store_articles

    metrics = {
        "provider": provider,
        "status": "ok",
        "stories_considered": len(target_stories),
        "stories_targeted": [],
        "feeds_attempted": len(feed_urls),
        "feeds_with_articles": 0,
        "feed_articles_scanned": 0,
        "input_articles": 0,
        "matched_articles": 0,
        "stored": 0,
        "new_outlets": 0,
        "stories_touched": 0,
        "skipped": {},
        "scrape_statuses": {},
        "bias_buckets": {},
        "bias_sources": {},
        "per_feed": [],
    }

    if not target_stories:
        metrics["status"] = "skipped_no_target_stories"
        return metrics

    recent_cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)
    feed_articles = []
    for feed_url in feed_urls:
        source_name, articles = fetch_feed(feed_url)
        recent_articles = [article for article in articles if article.get("published_at") and article["published_at"] >= recent_cutoff]
        if recent_articles:
            metrics["feeds_with_articles"] += 1
        metrics["per_feed"].append({
            "feed_url": feed_url,
            "source_name": source_name,
            "input_articles": len(recent_articles),
        })
        feed_articles.extend(recent_articles)

    if not feed_articles:
        metrics["status"] = "skipped_no_recent_feed_articles"
        return metrics

    matched_articles = []
    matched_urls = set()
    for story in target_stories:
        story_matches = []
        for article_data in feed_articles:
            article_url = article_data.get("url")
            if not article_url or article_url in matched_urls:
                continue
            if _feed_article_matches_story(article_data, story):
                story_matches.append(article_data)

        story_matches = story_matches[:max_articles_per_story]
        for article_data in story_matches:
            matched_urls.add(article_data["url"])
            matched_articles.append(article_data)

        metrics["stories_targeted"].append({
            "story_id": story.id,
            "title": story.display_headline,
            "existing_bias": dict(_story_bias_counts(story)),
            "matched_articles": len(story_matches),
            "keywords": sorted(_story_keyword_tokens(story)),
        })

    metrics["feed_articles_scanned"] = len(feed_articles)
    metrics["matched_articles"] = len(matched_articles)
    metrics["input_articles"] = len(matched_articles)

    if not matched_articles:
        metrics["status"] = "skipped_no_story_matches"
        return metrics

    store_metrics = store_articles(matched_articles, topic_name, provider=provider)
    metrics["stored"] = store_metrics.get("stored", 0)
    metrics["new_outlets"] = store_metrics.get("new_outlets", 0)
    metrics["stories_touched"] = store_metrics.get("stories_touched", 0)
    merge_count_maps(metrics["skipped"], store_metrics.get("skipped"))
    merge_count_maps(metrics["scrape_statuses"], store_metrics.get("scrape_statuses"))
    merge_count_maps(metrics["bias_buckets"], store_metrics.get("bias_buckets"))
    merge_count_maps(metrics["bias_sources"], store_metrics.get("bias_sources"))
    return metrics


def enrich_skewed_stories_with_right_feeds(
    max_stories=8,
    max_articles_per_story=3,
    story_age_days=3,
    lookback_hours=72,
    candidate_story_ids=None,
):
    """
    For top-ranked stories that are missing center-right/right coverage,
    search a small RSS-only center-right/right feed set and ingest matches.
    """
    from aggregator.models import Story

    right_feed_urls = _active_feed_urls("right_enrichment")
    metrics = {
        "provider": "rss_enrichment_right",
        "status": "ok",
        "stories_considered": 0,
        "stories_targeted": [],
        "feeds_attempted": len(right_feed_urls),
        "feeds_with_articles": 0,
        "feed_articles_scanned": 0,
        "input_articles": 0,
        "matched_articles": 0,
        "stored": 0,
        "new_outlets": 0,
        "stories_touched": 0,
        "skipped": {},
        "scrape_statuses": {},
        "bias_buckets": {},
        "bias_sources": {},
        "per_feed": [],
    }

    story_cutoff = datetime.utcnow() - timedelta(days=story_age_days)
    story_query = Story.query.filter(
        Story.headline_score > 0,
        Story.created_at >= story_cutoff,
    )
    if candidate_story_ids:
        story_query = story_query.filter(Story.id.in_(candidate_story_ids))
    story_candidates = (
        story_query
        .order_by(Story.headline_score.desc())
        .limit(max_stories * 4 if not candidate_story_ids else max(len(candidate_story_ids), max_stories))
        .all()
    )
    metrics["stories_considered"] = len(story_candidates)

    target_stories = []
    for story in story_candidates:
        if not _story_needs_right_enrichment(story):
            continue
        target_stories.append(story)
        if len(target_stories) >= max_stories:
            break

    metrics = _enrich_stories_with_feed_set(
        target_stories,
        right_feed_urls,
        provider="rss_enrichment_right",
        topic_name=RIGHT_ENRICHMENT_TOPIC,
        max_articles_per_story=max_articles_per_story,
        lookback_hours=lookback_hours,
    )
    metrics["stories_considered"] = len(story_candidates)
    return metrics


def enrich_skewed_stories_with_left_feeds(
    max_stories=8,
    max_articles_per_story=3,
    story_age_days=3,
    lookback_hours=72,
    candidate_story_ids=None,
):
    """
    For top-ranked stories that are missing left/center-left coverage,
    search a small RSS-only left/center-left feed set and ingest matches.
    """
    from aggregator.models import Story

    story_cutoff = datetime.utcnow() - timedelta(days=story_age_days)
    story_query = Story.query.filter(
        Story.headline_score > 0,
        Story.created_at >= story_cutoff,
    )
    if candidate_story_ids:
        story_query = story_query.filter(Story.id.in_(candidate_story_ids))
    story_candidates = (
        story_query
        .order_by(Story.headline_score.desc())
        .limit(max_stories * 4 if not candidate_story_ids else max(len(candidate_story_ids), max_stories))
        .all()
    )

    target_stories = []
    for story in story_candidates:
        if not _story_needs_left_enrichment(story):
            continue
        target_stories.append(story)
        if len(target_stories) >= max_stories:
            break

    metrics = _enrich_stories_with_feed_set(
        target_stories,
        _active_feed_urls("left_enrichment"),
        provider="rss_enrichment_left",
        topic_name=LEFT_ENRICHMENT_TOPIC,
        max_articles_per_story=max_articles_per_story,
        lookback_hours=lookback_hours,
    )
    metrics["stories_considered"] = len(story_candidates)
    return metrics


def enrich_story_with_opposite_feeds(story, max_articles_per_story=3, lookback_hours=72):
    counts = _story_bias_counts(story)
    leftish_total = counts["left"] + counts["lean_left"]
    rightish_total = counts["right"] + counts["lean_right"]

    if leftish_total > rightish_total:
        direction = "right"
        feed_urls = _active_feed_urls("right_enrichment")
        provider = "rss_enrichment_right_manual"
        topic_name = RIGHT_ENRICHMENT_TOPIC
    elif rightish_total > leftish_total:
        direction = "left"
        feed_urls = _active_feed_urls("left_enrichment")
        provider = "rss_enrichment_left_manual"
        topic_name = LEFT_ENRICHMENT_TOPIC
    else:
        return {
            "provider": "rss_enrichment_manual",
            "status": "skipped_balanced_story",
            "direction": "none",
            "stories_considered": 1,
            "stories_targeted": [],
            "feeds_attempted": 0,
            "feeds_with_articles": 0,
            "feed_articles_scanned": 0,
            "input_articles": 0,
            "matched_articles": 0,
            "stored": 0,
            "new_outlets": 0,
            "stories_touched": 0,
            "skipped": {},
            "scrape_statuses": {},
            "bias_buckets": {},
            "bias_sources": {},
            "per_feed": [],
        }

    metrics = _enrich_stories_with_feed_set(
        [story],
        feed_urls,
        provider=provider,
        topic_name=topic_name,
        max_articles_per_story=max_articles_per_story,
        lookback_hours=lookback_hours,
    )
    metrics["direction"] = direction
    return metrics
