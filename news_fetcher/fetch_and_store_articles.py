# muckscraperHeadlinesGoogleNEW/news_fetcher/fetch_and_store_articles.py
# news_fetcher/fetch_and_store_articles.py

from aggregator import create_app, db
from aggregator.article_signals import ROUNDUP_TITLE_PATTERNS, bias_bucket_for_score, is_roundup_article, low_value_article_reason
from aggregator.models import Article, Outlet, Story, Topic
from newsapi import NewsApiClient
from news_fetcher.outlet_bias_llm import get_outlet_bias_from_llm
from news_fetcher.allsides_lookup import get_allsides_score
from news_fetcher.summarizer import summarize_story, check_ollama_status, generate_deep_report, summarize_article
from news_fetcher.scraper import scrape_article
from datetime import datetime
import requests
import os
import json
import re
from news_fetcher.story_grouper import find_or_create_story, get_embedding, normalize_title_tokens, titles_are_near_duplicates
from datetime import datetime, timedelta
from news_fetcher.topic_classifier import classify_article
from news_fetcher.headline_generator import generate_story_headline, generate_missing_headlines
import logging
from sqlalchemy.orm import selectinload
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

app = create_app()

BLOCKED_SOURCES = [
    "github.com",
    "github.blog",
    "dev.to",
    "stackoverflow.com",
    "reddit.com",
    "npmjs.com",
    "pypi.org",
    # Sports-betting tipster sites -- their entire output is odds and picks, so
    # block at the domain rather than relying on title matching. Mainstream
    # outlets that also run betting content are handled by
    # article_signals.BETTING_TITLE_PATTERNS instead, since only part of their
    # output qualifies.
    "actionnetwork.com",
    "vsin.com",
    "covers.com",
    "sportsline.com",
    "lineups.com",
]

BLOCKED_TITLE_KEYWORDS = [
    "starred",
    "forked",
    "pull request",
    "merged",
    "repository",
    "npm package",
    "pypi",
    "added to pypi",
    "released on pypi",
    "week in review",
    "patch tuesday",
    "added to npm",
    "new release:",
    "changelog:",
    "box office",
    "box score",
    "game recap",
    "highlights:",
    "traded to",
    "signs with",
    "scores in",
    "Nintendo",
    "PlayStation",
    "Xbox",
    "Game review",
    "Gameplay",
    "eSports",
    "patch notes",
    "Twitch",
    "Fortnite",
    "Minecraft",
    "Pokemon",
]

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


def is_generic_roundup_title(title):
    normalized = (title or "").strip()
    return any(pattern.search(normalized) for pattern in ROUNDUP_TITLE_PATTERNS)


def guess_story_title(title):
    if ":" in title:
        return title.split(":")[0]
    if "-" in title:
        return title.split("-")[0]
    return " ".join(title.split()[:6])


def clear_story_headline_if_single_article(story):
    """Single-article stories should fall back to their original title."""
    if story and len(story.articles) <= 1:
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


def _story_dedupe_titles(story, max_article_titles=5):
    titles = []
    if story.title:
        titles.append(story.title)
    if story.headline and story.headline != story.title:
        titles.append(story.headline)
    for article in story.articles[:max_article_titles]:
        if article.title:
            titles.append(article.title)
    return titles


def _story_signature_tokens(story):
    tokens = set()
    for title in _story_dedupe_titles(story):
        tokens.update(normalize_title_tokens(title))
    return tokens


EDITION_DEDUPE_GENERIC_TOKENS = {
    "advances",
    "around",
    "asks",
    "call",
    "calls",
    "crowds",
    "deal",
    "desperate",
    "early",
    "experts",
    "faces",
    "gather",
    "guy",
    "her",
    "here",
    "house",
    "inside",
    "just",
    "live",
    "meets",
    "month",
    "more",
    "out",
    "panel",
    "press",
    "questions",
    "readies",
    "release",
    "releases",
    "running",
    "say",
    "scepticism",
    "security",
    "seeks",
    "senate",
    "spotlight",
    "talks",
    "test",
    "tight",
    "visit",
    "vote",
    "wire",
    "where",
    "wife",
    "win",
}


def _distinctive_shared_tokens(tokens_a, tokens_b):
    return {
        token for token in (tokens_a & tokens_b)
        if token not in EDITION_DEDUPE_GENERIC_TOKENS
    }


def stories_look_duplicate_for_edition(story_a, story_b):
    titles_a = _story_dedupe_titles(story_a)
    titles_b = _story_dedupe_titles(story_b)

    for title_a in titles_a:
        for title_b in titles_b:
            if titles_are_near_duplicates(title_a, title_b):
                return True

    tokens_a = _story_signature_tokens(story_a)
    tokens_b = _story_signature_tokens(story_b)
    shared_tokens = tokens_a & tokens_b
    distinctive_shared = _distinctive_shared_tokens(tokens_a, tokens_b)
    if len(distinctive_shared) >= 4:
        return True
    if len(distinctive_shared) >= 3:
        return True

    outlets_a = {((article.outlet.name or "").strip().lower()) for article in story_a.articles if article.outlet and article.outlet.name}
    outlets_b = {((article.outlet.name or "").strip().lower()) for article in story_b.articles if article.outlet and article.outlet.name}
    if outlets_a & outlets_b and len(distinctive_shared) >= 2 and len(shared_tokens) >= 3:
        return True

    return False


def _story_balance_bucket(story):
    counts = {
        "leftish": 0,
        "center": 0,
        "rightish": 0,
        "unrated": 0,
    }
    for article in story.articles:
        score = article.bias_score
        if score is None and article.outlet:
            score = article.outlet.bias_score
        bucket = bias_bucket_for_score(score)
        if bucket in ("left", "lean_left"):
            counts["leftish"] += 1
        elif bucket in ("right", "lean_right"):
            counts["rightish"] += 1
        elif bucket == "center":
            counts["center"] += 1
        else:
            counts["unrated"] += 1

    leftish = counts["leftish"]
    center = counts["center"]
    rightish = counts["rightish"]
    rated_total = leftish + center + rightish

    if rated_total == 0:
        return "unrated"

    # A story with comparable left/right coverage should not be treated as
    # ideologically dominated just because of dict insertion order.
    if leftish and rightish and abs(leftish - rightish) <= 1:
        return "center"

    if center >= leftish and center >= rightish:
        return "center"
    if rightish > leftish:
        return "rightish"
    return "leftish"


def _story_has_left_and_right_coverage(story):
    has_leftish = False
    has_rightish = False

    for article in story.articles:
        score = article.bias_score
        if score is None and article.outlet:
            score = article.outlet.bias_score
        bucket = bias_bucket_for_score(score)

        if bucket in ("left", "lean_left"):
            has_leftish = True
        elif bucket in ("right", "lean_right"):
            has_rightish = True

        if has_leftish and has_rightish:
            return True

    return False


def _story_primary_outlet(story):
    outlet_counts = {}
    for article in story.articles:
        if not article.outlet or not article.outlet.name:
            continue
        key = article.outlet.name.strip()
        if not key:
            continue
        outlet_counts[key] = outlet_counts.get(key, 0) + 1
    if not outlet_counts:
        return "unknown"
    return max(sorted(outlet_counts.items()), key=lambda item: item[1])[0]


def _first_story_article(story):
    for article in story.articles:
        if article is not None:
            return article
    return None


def retry_unrated_outlets():
    """Find outlets with no bias score and retry.
    Checks AllSides lookup table first, then falls back to Ollama.
    Outlets that have failed Ollama 15 or more times are permanently skipped.
    """
    unrated = Outlet.query.filter(
        Outlet.bias_score == None,
        Outlet.bias_retry_count < 15
    ).all()

    if not unrated:
        logger.info("No unrated outlets to retry.")
        return

    skipped = Outlet.query.filter(
        Outlet.bias_score == None,
        Outlet.bias_retry_count >= 15
    ).count()

    if skipped:
        logger.info(f"Permanently skipping {skipped} outlets that have failed 15+ times.")

    logger.info(f"Found {len(unrated)} unrated outlets, checking AllSides then Ollama...")

    for outlet in unrated:
        # Check AllSides lookup table first
        as_score = get_allsides_score(outlet.name)
        if as_score is not None:
            logger.info(f"  AllSides rating found for {outlet.name}: {as_score}")
            outlet.bias_score = as_score
            outlet.allsides_bias_score = as_score
            outlet.bias_source = "allsides"
            outlet.bias_retry_count = 0
            for article in outlet.articles:
                article.bias_score = as_score
            continue

        # Fall back to Ollama
        logger.info(f"  No AllSides rating for {outlet.name}, trying Ollama...")
        bias_score = get_outlet_bias_from_llm(outlet.name)

        if bias_score is not None:
            logger.info(f"  Ollama score {bias_score} for {outlet.name}")
            outlet.bias_score = bias_score
            outlet.bias_source = "ai"
            outlet.bias_retry_count = 0
            for article in outlet.articles:
                article.bias_score = bias_score
        else:
            outlet.bias_retry_count = (outlet.bias_retry_count or 0) + 1
            logger.warning(
                f"  Still couldn't rate {outlet.name} "
                f"(attempt {outlet.bias_retry_count}/15)."
            )

    db.session.commit()
    logger.info("Finished retrying unrated outlets.")


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


# Domain -> canonical outlet name overrides. Used when a feed's channel
# <title> can't be trusted to identify the outlet -- e.g. the Washington
# Post's world-section RSS feed has a channel title of literally "World",
# which normalize_source_name() has no way to map back to "Washington Post".
# The article's own URL is a much more reliable signal than a feed-level
# title, so this takes priority over normalize_source_name() in store_articles().
DOMAIN_SOURCE_OVERRIDES = {
    "washingtonpost.com": "Washington Post",
}


def source_name_from_url(url):
    """Look up a canonical outlet name by the article URL's domain, if known."""
    if not url:
        return None
    domain = urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return DOMAIN_SOURCE_OVERRIDES.get(domain)


def normalize_source_name(name):
    """Clean up and standardize outlet names."""
    if not name:
        return "Unknown"

    name_lower = name.lower().strip()

    # Define normalization map
    mapping = {
        "npr topics": "NPR",
        "home - cbsnews.com": "CBS News",
        "pbs newshour": "PBS News",
        "the associated press": "Associated Press",
        "fox news": "Fox News",
        "abc news": "ABC News",
        "nbc news": "NBC News",
        "the wall street journal": "WSJ",
        "the new york times": "New York Times",
        "the washington post": "Washington Post",
        
    }

    # Direct match in mapping
    if name_lower in mapping:
        return mapping[name_lower]

    # Partial matches/cleaning

    # Al Jazeera — strip long feed title
    if "al jazeera" in name_lower:
        return "Al Jazeera"

    # The Hill — strip " news" suffix
    if "the hill" in name_lower:
        return "The Hill"

    # New York Times variants
    if "nyt" in name_lower or "new york times" in name_lower:
        return "New York Times"

    # The Guardian variants
    if "guardian" in name_lower:
        return "The Guardian"

    # AP / Associated Press
    if "associated press" in name_lower or name_lower == "ap news":
        return "Associated Press"

    # Google News — flag as aggregator
    if name_lower == "google news":
        return "Google News"

    # Reuters variants
    if "reuters" in name_lower:
        return "Reuters"

    # Washington Post variants
    if "washington post" in name_lower:
        return "Washington Post"

    # Washington Times RSS section titles
    if "washington times" in name_lower:
        return "The Washington Times"

    # Wall Street Journal variants  
    if "wall street journal" in name_lower or name_lower == "wsj":
        return "WSJ"

    # NBC variants — keep NBCSports separate
    if "nbc news" in name_lower:
        return "NBC News"
    if "nbcsports" in name_lower or "nbc sports" in name_lower:
        return "NBC Sports"

    # CBS variants
    if "cbs news" in name_lower:
        return "CBS News"

    # PBS variants
    if "pbs" in name_lower and "news" in name_lower:
        return "PBS News"

    # ABC News
    if "abc news" in name_lower:
        return "ABC News"

    # NPR variants
    if "npr" in name_lower:
        return "NPR"

    # BBC variants
    if "bbc" in name_lower:
        return "BBC News"

    # International right-leaning RSS variants
    if "national post" in name_lower:
        return "National Post"
    if "telegraph" in name_lower and "india" not in name_lower:
        return "The Telegraph"
    if "toronto sun" in name_lower:
        return "Toronto Sun"

    # Fox News — keep Fox Business separate
    if "fox news" in name_lower:
        return "Fox News"
    if "fox business" in name_lower:
        return "Fox Business"

    # Bloomberg
    if "bloomberg" in name_lower:
        return "Bloomberg"

    # Axios
    if "axios" in name_lower:
        return "Axios"

    # CNN
    if name_lower == "cnn" or name_lower.startswith("cnn "):
        return "CNN"

    # CNBC
    if "cnbc" in name_lower and "tv18" not in name_lower:
        return "CNBC"

    return name


def merge_duplicate_outlets():
    """
    One-time (and periodic) cleanup:
    1. Re-normalizes all outlet names using normalize_source_name().
    2. Finds outlets whose normalized name matches another outlet.
    3. Merges duplicates — reassigns all articles to the canonical outlet,
       then deletes the duplicate.
    Returns a summary dict with counts for logging/display.
    """
    from aggregator.models import Outlet, Article

    outlets = Outlet.query.all()
    renamed = 0
    merged = 0
    deleted = 0

    # Step 1: Normalize all names in-place
    for outlet in outlets:
        clean = normalize_source_name(outlet.name)
        if clean != outlet.name:
            logger.info(f"  [Merge] Renaming '{outlet.name}' → '{clean}'")
            outlet.name = clean
            renamed += 1

    db.session.flush()

    # Step 2: Find duplicates by name (case-insensitive)
    # For each group of outlets with the same normalized name,
    # keep the one with the most articles (canonical), merge the rest into it.
    outlets = Outlet.query.all()
    name_map = {}
    for outlet in outlets:
        key = outlet.name.lower().strip()
        if key not in name_map:
            name_map[key] = []
        name_map[key].append(outlet)

    for name_key, group in name_map.items():
        if len(group) <= 1:
            continue

        # Canonical = outlet with the most articles
        canonical = max(group, key=lambda o: len(o.articles))
        duplicates = [o for o in group if o.id != canonical.id]

        for dup in duplicates:
            article_count = Article.query.filter_by(outlet_id=dup.id).count()
            logger.info(
                f"  [Merge] Merging '{dup.name}' (id={dup.id}, "
                f"{article_count} articles) → '{canonical.name}' (id={canonical.id})"
            )

            # CRITICAL: Reassign articles BEFORE deleting the outlet.
            # Use direct SQL update to avoid SQLAlchemy session conflicts
            # that can cause articles to be orphaned.
            db.session.execute(
                db.text(
                    "UPDATE articles SET outlet_id = :canonical_id "
                    "WHERE outlet_id = :dup_id"
                ),
                {"canonical_id": canonical.id, "dup_id": dup.id}
            )
            db.session.flush()

            # Verify reassignment before deleting
            remaining = Article.query.filter_by(outlet_id=dup.id).count()
            if remaining > 0:
                logger.error(
                    f"  [Merge] ABORT: {remaining} articles still attached to "
                    f"'{dup.name}' after reassignment — skipping delete"
                )
                continue

            # Copy bias data if canonical is missing it
            if canonical.bias_score is None and dup.bias_score is not None:
                canonical.bias_score = dup.bias_score
                canonical.bias_source = dup.bias_source
                canonical.allsides_bias_score = getattr(dup, 'allsides_bias_score', None)

            db.session.delete(dup)
            merged += article_count
            deleted += 1

    db.session.commit()

    summary = {
        'renamed': renamed,
        'outlets_deleted': deleted,
        'articles_reassigned': merged,
    }
    logger.info(f"  [Merge] Complete: {summary}")
    return summary


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
    cutoff = datetime.utcnow() - timedelta(days=GROUPING_LOOKBACK_DAYS)
    recent_stories = (
        Story.query
        .options(selectinload(Story.articles))
        .filter(Story.created_at >= cutoff)
        .all()
    )
    logger.info(
        "  [Grouper] Loaded %s recent stories from the last %s day(s) for matching",
        len(recent_stories),
        GROUPING_LOOKBACK_DAYS,
    )

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

        if any(blocked in url.lower() for blocked in BLOCKED_SOURCES):
            logger.debug(f"Skipping blocked source: {url}")
            metrics["skipped"]["blocked_source"] += 1
            continue

        low_value_reason = low_value_article_reason(title, url)
        if low_value_reason:
            logger.debug(f"Skipping low-value article ({low_value_reason}): {title} [{url}]")
            metrics["skipped"][low_value_reason if low_value_reason in metrics["skipped"] else "low_value_url"] += 1
            continue

        if any(kw.lower() in title.lower() for kw in BLOCKED_TITLE_KEYWORDS):
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

        # Generate headline if this is a multi-article story (2+ articles)
        if len(story.articles) >= 2:
            db.session.flush() # Ensure article is associated for headline generator
            headline = generate_story_headline(story)
            if headline:
                story.headline = headline
        else:
            # For single-article stories, ensure story headline is cleared
            # so the UI falls back to story.title (original article title)
            story.headline = None
                
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

    if not check_ollama_status():
        logger.info("Ollama offline, skipping ambiguous grouping review.")
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

        decision = find_matching_story_with_metadata(
            article.title,
            article.embedding,
            candidate_stories,
            article_content=article.content,
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
            article.story_id = matched_story.id
            db.session.flush()
            reassigned += 1

            for topic in list(original_story.topics) if original_story else []:
                if topic not in matched_story.topics:
                    matched_story.topics.append(topic)

            if original_story:
                clear_story_headline_if_single_article(original_story)

            if original_story and not original_story.articles:
                db.session.delete(original_story)

            if len(matched_story.articles) >= 2:
                headline = generate_story_headline(matched_story)
                if headline:
                    matched_story.headline = headline

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


def fetch_newsapi(topic_name, mode="top", query=None, country="us", category=None):
    """Fetch articles from NewsAPI and store them."""
    api_key = os.environ.get("NEWS_API_KEY", "")
    if not api_key:
        logger.warning("NEWS_API_KEY not set, skipping NewsAPI fetch.")
        return {
            "provider": "newsapi",
            "topic_name": topic_name,
            "status": "skipped",
            "reason": "missing_api_key",
            "input_articles": 0,
            "stored": 0,
        }

    newsapi = NewsApiClient(api_key=api_key)

    try:
        if mode == "query" and query:
            logger.info(f"[NewsAPI] Fetching query: {query}")
            results = newsapi.get_everything(
                q=query,
                language="en",
                sort_by="publishedAt",
                page_size=100,
            )
        else:
            label = f"country={country}" if country else ""
            label += f" category={category}" if category else ""
            logger.info(f"[NewsAPI] Fetching top headlines ({label.strip()})")
            kwargs = {"page_size": 100}
            if country:
                kwargs["country"] = country
            if category:
                kwargs["category"] = category
            results = newsapi.get_top_headlines(**kwargs)

        raw_articles = results.get("articles", [])
        logger.info(f"[NewsAPI] Fetched {len(raw_articles)} articles")

        normalized = []
        for a in raw_articles:
            published_at_str = a.get("publishedAt")
            try:
                published_at = datetime.fromisoformat(
                    published_at_str.replace("Z", "+00:00")
                ).replace(tzinfo=None) if published_at_str else datetime.utcnow()
            except Exception:
                published_at = datetime.utcnow()

            normalized.append({
                "title":        a.get("title"),
                "content":      a.get("content") or "",
                "url":          a.get("url"),
                "source_name":  (a.get("source") or {}).get("name", "Unknown"),
                "published_at": published_at,
                "image_url":    a.get("urlToImage"),
            })

        metrics = store_articles(normalized, topic_name, provider="newsapi")

        # Store raw payload
        from aggregator.models import RawArticlePayload
        raw = RawArticlePayload(
            source="newsapi",
            topic_name=topic_name,
            payload=json.dumps(results),
        )
        db.session.add(raw)
        db.session.commit()
        metrics["status"] = "ok"
        return metrics

    except Exception as e:
        db.session.rollback()
        logger.error(f"[NewsAPI] Error fetching {topic_name}: {e}")
        return {
            "provider": "newsapi",
            "topic_name": topic_name,
            "status": "error",
            "reason": str(e),
            "input_articles": 0,
            "stored": 0,
        }


def fetch_gnews(topic_name, query=None, category=None):
    """Fetch articles from GNews API and store them."""
    api_key = os.environ.get("GNEWS_API_KEY", "")
    if not api_key:
        logger.warning("GNEWS_API_KEY not set, skipping GNews fetch.")
        return {
            "provider": "gnews",
            "topic_name": topic_name,
            "status": "skipped",
            "reason": "missing_api_key",
            "input_articles": 0,
            "stored": 0,
        }

    try:
        if query:
            logger.info(f"[GNews] Fetching query: {query}")
            url = "https://gnews.io/api/v4/search"
            params = {
                "q":      query,
                "lang":   "en",
                "max":    20,
                "apikey": api_key,
            }
        elif category:
            logger.info(f"[GNews] Fetching category: {category}")
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                "category": category,
                "lang":     "en",
                "country":  "us",
                "max":      20,
                "apikey":   api_key,
            }
        else:
            logger.info(f"[GNews] Fetching top headlines")
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                "lang":    "en",
                "country": "us",
                "max":     20,
                "apikey":  api_key,
            }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        raw_articles = data.get("articles", [])
        logger.info(f"[GNews] Fetched {len(raw_articles)} articles")

        normalized = []
        for a in raw_articles:
            published_at_str = a.get("publishedAt")
            try:
                published_at = datetime.fromisoformat(
                    published_at_str.replace("Z", "+00:00")
                ).replace(tzinfo=None) if published_at_str else datetime.utcnow()
            except Exception:
                published_at = datetime.utcnow()

            source = a.get("source") or {}
            normalized.append({
                "title":        a.get("title"),
                "content":      a.get("content") or a.get("description") or "",
                "url":          a.get("url"),
                "source_name":  source.get("name", "Unknown"),
                "published_at": published_at,
                "image_url":    a.get("image"),
            })

        metrics = store_articles(normalized, topic_name, provider="gnews")

        # Store raw payload
        from aggregator.models import RawArticlePayload
        raw = RawArticlePayload(
            source="gnews",
            topic_name=topic_name,
            payload=json.dumps(data),
        )
        db.session.add(raw)
        db.session.commit()
        metrics["status"] = "ok"
        return metrics

    except Exception as e:
        db.session.rollback()
        logger.error(f"[GNews] Error fetching {topic_name}: {e}")
        return {
            "provider": "gnews",
            "topic_name": topic_name,
            "status": "error",
            "reason": str(e),
            "input_articles": 0,
            "stored": 0,
        }
    

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

            # Move article to matched story
            article.story_id = matched.id
            db.session.flush()

            # Merge topic tags
            for topic in story.topics:
                if topic not in matched.topics:
                    matched.topics.append(topic)

            # Generate/Update headline for the matched story now that it has a new article
            from news_fetcher.headline_generator import generate_story_headline
            headline = generate_story_headline(matched)
            if headline:
                matched.headline = headline

            # Delete the now-empty story
            db.session.delete(story)
            merged += 1

    db.session.commit()
    logger.info(f"Re-grouping complete. Merged {merged} stories.")


def generate_missing_deep_reports(batch_size=5):
    """Find multi-article stories picked for headlines that don't have deep reports."""
    if not check_ollama_status():
        logger.info("Ollama offline, skipping deep report generation.")
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


def generate_missing_embeddings(batch_size=50):
    """Generate embeddings for articles that don't have one yet."""
    from news_fetcher.story_grouper import get_embedding

    missing = Article.query.filter(Article.embedding == None).limit(batch_size).all()

    if not missing:
        logger.info("All articles have embeddings.")
        return

    logger.info(f"Generating embeddings for {len(missing)} articles...")
    count = 0
    for article in missing:
        # Align with store_articles and force_regroup_all: use title + snippet
        from news_fetcher.story_grouper import strip_video_prefix
        clean_title = strip_video_prefix(article.title)
        embed_text = clean_title
        if article.content:
            from news_fetcher.summarizer import strip_html
            snippet = strip_html(article.content)[:200].strip()
            embed_text = f"{clean_title}. {snippet}"
        embedding = get_embedding(embed_text)
        if embedding is not None:
            article.embedding = embedding
            count += 1

    db.session.commit()
    logger.info(f"Generated {count} embeddings.")


def audit_existing_scrapes(batch_size=200):
    """
    Scan non-audited article content for bad scrapes — login walls, captchas,
    bot detection pages, and outlet-level duplicate content.
    Clears bad content and adds offending domains to the blocklist.
    """
    from news_fetcher.scraper import detect_bad_scrape, get_domain, add_to_blocklist
    import re

    articles = Article.query.filter(
        Article.scrape_audited == False,
        Article.content != None,
        Article.content != ""
    ).order_by(Article.outlet_id, Article.id).all()

    if not articles:
        logger.info("[Audit] No new articles to audit.")
        return

    logger.info(f"[Audit] Scanning {len(articles)} new articles for bad scrapes...")

    cleared = 0
    auto_blocked = set()

    for i, article in enumerate(articles):
        # Mark as audited immediately
        article.scrape_audited = True

        if not article.content:
            continue

        domain = get_domain(article.url)

        # If domain was already flagged this run, just clear the content
        if domain and domain in auto_blocked:
            article.content = None
            article.scrape_status = "blocked"
            article.scrape_failure_reason = "domain_auto_blocked_same_audit_run"
            cleared += 1
            continue

        # Strong/weak indicator check
        is_bad, reason = detect_bad_scrape(article.content)
        if is_bad:
            logger.info(f"  [Audit] Bad scrape detected: {article.title[:60]} — {reason}")
            article.content = None
            article.scrape_status = "blocked"
            article.scrape_failure_reason = reason
            cleared += 1
            if domain:
                add_to_blocklist(article.url, reason)
                auto_blocked.add(domain)
            continue

        # Duplicate content check
        is_dup, dup_reason = detect_duplicate_outlet_content(
            article.content, article.outlet_id, exclude_article_id=article.id
        )
        if is_dup:
            logger.info(f"  [Audit] Duplicate scrape detected: {article.title[:60]} — {dup_reason}")
            article.content = None
            article.scrape_status = "blocked"
            article.scrape_failure_reason = dup_reason
            cleared += 1
            if domain:
                add_to_blocklist(article.url, dup_reason)
                auto_blocked.add(domain)
            continue

        # Commit in batches
        if (i + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"  [Audit] Progress: {i + 1}/{len(articles)}, cleared {cleared} so far")

    db.session.commit()
    logger.info(f"[Audit] Complete. Cleared {cleared} articles, auto-blocked {len(auto_blocked)} domains.")


def force_resummarize_all(batch_size=20):
    """
    Force re-generate summaries and deep reports for all stories and articles
    using the updated specialized journalist personas.
    """
    if not check_ollama_status():
        logger.info("Ollama offline, skipping force re-summarization.")
        return

    logger.info("=== Force re-summarization starting ===")
    
    # 1. Update Story Summaries
    stories = Story.query.all()
    logger.info(f"Re-summarizing {len(stories)} stories...")
    for i, story in enumerate(stories):
        if not story.articles:
            continue
        summary = summarize_story(story)
        if summary:
            story.summary = summary
        
        if (i + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"  Progress (Stories): {i+1}/{len(stories)}")
    
    db.session.commit()

    # 2. Update Deep Reports
    from sqlalchemy import func
    multi_article_stories = Story.query.join(Article).group_by(Story.id).having(
        func.count(Article.id) >= 2
    ).all()
    logger.info(f"Re-analyzing {len(multi_article_stories)} multi-article stories (Deep Reports)...")
    for i, story in enumerate(multi_article_stories):
        report = generate_deep_report(story)
        if report:
            story.deep_report = report
        
        if (i + 1) % 5 == 0: # Deep reports are slower
            db.session.commit()
            logger.info(f"  Progress (Deep Reports): {i+1}/{len(multi_article_stories)}")
            
    db.session.commit()

    # 3. Update Article Summaries
    articles = Article.query.filter(Article.content != None).all()
    logger.info(f"Re-summarizing {len(articles)} articles...")
    for i, article in enumerate(articles):
        summary = summarize_article(article)
        if summary:
            article.summary = summary
        
        if (i + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"  Progress (Articles): {i+1}/{len(articles)}")

    db.session.commit()
    logger.info("=== Force re-summarization complete ===")


def force_regroup_all():
    """
    Force re-group ALL articles using vector similarity embeddings.
    Regenerates ALL embeddings first (to include content), then re-assigns every article
    to the best matching story.
    """
    from news_fetcher.story_grouper import get_embedding, find_matching_story

    if not check_ollama_status():
        logger.info("Ollama offline, skipping force re-group.")
        return

    logger.info("=== Force re-group starting ===")
    logger.info("  [Force Regroup] Step 1: Regenerating embeddings...")

    # Step 1: Regenerate embeddings for ALL articles to ensure content is included
    all_articles = Article.query.all()
    logger.info(f"Regenerating embeddings for {len(all_articles)} articles (this may take a while)...")
    
    for i, article in enumerate(all_articles):
        # Use title + snippet for better semantic matching
        from news_fetcher.story_grouper import strip_video_prefix
        clean_title = strip_video_prefix(article.title)
        embed_text = clean_title
        if article.content:
            from news_fetcher.summarizer import strip_html
            snippet = strip_html(article.content)[:200].strip()
            embed_text = f"{clean_title}. {snippet}"
        embedding = get_embedding(embed_text)
        if embedding is not None:
            article.embedding = embedding
        
        if (i + 1) % 50 == 0:
            db.session.commit()
            logger.info(f"  [Force Regroup] Embeddings progress: {i + 1}/{len(all_articles)}")

    db.session.commit()
    logger.info("Embeddings regenerated.")
    logger.info("  [Force Regroup] Step 2: Starting re-grouping loop...")

    # Step 2: Get all articles with embeddings (should be all of them now)
    # Re-query to be safe
    all_articles = Article.query.filter(Article.embedding != None).all()
    logger.info(f"Re-grouping {len(all_articles)} articles...")

    # Step 3: Delete all existing stories and re-create from scratch
    # First detach all articles from stories and clear topics
    for article in all_articles:
        article.story_id = None
        article.topics = [] # Clear in-memory topics to avoid IntegrityError on flush/commit
    db.session.flush()

    # Clear junction tables first to avoid foreign key violations
    db.session.execute(db.text("DELETE FROM story_topics"))
    db.session.execute(db.text("DELETE FROM article_topics"))
    db.session.flush()

    # Delete all stories
    Story.query.delete()
    db.session.flush()
    
    # CRITICAL: Expire all objects after bulk deletes so the identity map 
    # doesn't contain references to the deleted Story objects.
    db.session.expire_all()

    # Step 4: Re-group articles one by one and re-attach topics
    from news_fetcher.story_grouper import clean_story_title
    from news_fetcher.topic_classifier import classify_article
    from aggregator.models import Topic as TopicModel

    new_stories = []
    try:
        for i, article in enumerate(all_articles):
            matched = find_matching_story(
                article.title, article.embedding, new_stories, article_content=article.content
            )

            if matched:
                story = matched
            else:
                new_title = clean_story_title(article.title)
                story = Story(title=new_title, summary=None)
                db.session.add(story)
                db.session.flush()
                new_stories.append(story)
            
            # Re-attach article to story
            article.story = story
            # Maintain in-memory list so find_matching_story can see it
            if article not in story.articles:
                story.articles.append(article)

            # Re-attach topic tags
            topic_names = classify_article(article.title, article.content or "")
            for topic_name in topic_names:
                topic = TopicModel.query.filter_by(name=topic_name).first()
                if not topic:
                    topic = TopicModel(name=topic_name)
                    db.session.add(topic)
                    db.session.flush()
                
                # Since we cleared article.topics = [] above, this is safe
                if topic not in article.topics:
                    article.topics.append(topic)
                if topic not in story.topics:
                    story.topics.append(topic)

            # Commit in batches of 50
            if (i + 1) % 50 == 0:
                db.session.commit()
                logger.info(f"  [Force Regroup] Grouping progress: {i + 1}/{len(all_articles)}")

    except Exception as e:
        logger.error(f"  [Force Regroup] CRITICAL ERROR: {e}")
        import traceback
        logger.error(traceback.format_exc())
        db.session.rollback()
        raise

    db.session.commit()

    # Step 5: Generate headlines for all multi-article stories
    logger.info("Generating AI headlines for regrouped stories...")
    logger.info("  [Force Regroup] Step 3: Generating AI headlines...")
    generate_missing_headlines()

    logger.info(f"=== Force re-group complete. Created {len(new_stories)} stories. ===")


def reclassify_all_articles(batch_size=50):
    """
    Reclassify all existing articles into the new topic system using Ollama.
    Clears existing topic tags and reassigns based on content.
    """
    from news_fetcher.topic_classifier import classify_article
    from aggregator.models import Topic as TopicModel

    if not check_ollama_status():
        logger.info("Ollama offline, skipping reclassification.")
        return

    # Clear all existing topic assignments
    db.session.execute(db.text("DELETE FROM article_topics"))
    db.session.execute(db.text("DELETE FROM story_topics"))
    db.session.flush()
    db.session.expire_all() # Ensure stale collections are cleared
    logger.info("Cleared existing topic assignments.")

    all_articles = Article.query.all()
    total = len(all_articles)
    logger.info(f"Reclassifying {total} articles...")

    for i, article in enumerate(all_articles):
        # Clear in-memory topics for this article to be safe
        article.topics = []
        
        topic_names = classify_article(article.title, article.content or "")

        for topic_name in topic_names:
            topic = TopicModel.query.filter_by(name=topic_name).first()
            if not topic:
                topic = TopicModel(name=topic_name)
                db.session.add(topic)
                db.session.flush()
            
            if topic not in article.topics:
                article.topics.append(topic)
            
            if article.story:
                if topic not in article.story.topics:
                    article.story.topics.append(topic)

        # Commit in batches
        if (i + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"  Progress: {i + 1}/{total}")

    db.session.commit()
    logger.info(f"Reclassification complete. Processed {total} articles.")


def ollama_catchup():
    """
    Run all Ollama-dependent tasks that may have been skipped
    while Ollama was offline.
    """
    logger.info("=== Ollama catchup starting ===")
    audit_existing_scrapes()
    generate_missing_embeddings(batch_size=50)
    generate_missing_headlines()
    regroup_ungrouped_stories()
    retry_unrated_outlets()
    logger.info("=== Ollama catchup complete ===")


def cleanup_old_payloads():
    """Delete raw API payloads older than 30 days."""
    from aggregator.models import RawArticlePayload
    cutoff = datetime.utcnow() - timedelta(days=30)
    old = RawArticlePayload.query.filter(RawArticlePayload.fetched_at < cutoff).all()
    if old:
        logger.info(f"Deleting {len(old)} raw payloads older than 30 days...")
        for payload in old:
            db.session.delete(payload)
        db.session.commit()
        logger.info("Cleanup complete.")
    else:
        logger.info("No old payloads to clean up.")


def fetch_and_store_articles(topic_name, mode="top", query=None,
                              country="us", category=None,
                              gnews_query=None, gnews_category=None):
    """
    Main entry point. Fetches from both NewsAPI and GNews for a given topic.
    """
    newsapi_metrics = fetch_newsapi(topic_name, mode=mode, query=query,
                                    country=country, category=category)
    gnews_metrics = fetch_gnews(topic_name, query=gnews_query, category=gnews_category)
    cleanup_old_payloads()
    return {
        "topic_name": topic_name,
        "providers": {
            "newsapi": newsapi_metrics,
            "gnews": gnews_metrics,
        },
    }


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


def _fill_story_content(story, metrics):
    """
    Generate any missing summary/deep-report/child-article-summary content
    for a single story. Returns True if anything was generated or reset.
    """
    from news_fetcher.summarizer import (
        summarize_story,
        generate_deep_report,
        summarize_article,
        generate_article_deep_analysis,
        article_needs_deep_analysis,
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
            if new_article_count >= STALE_ARTICLE_THRESHOLD:
                logger.info(
                    f"  [Processor] Story stale ({new_article_count} new articles "
                    f"since last analysis): {story.title[:60]}"
                )
                story.summary = None
                story.deep_report = None
                story_is_stale = True
                metrics["stale_stories_reset"] += 1
                changed = True

        if article_count >= 2:
            story_outputs_ready = bool(story.summary) and bool(story.deep_report)
        else:
            story_outputs_ready = bool(story.summary)

        missing_child_summaries = any(
            article.content and not article.summary
            for article in story.articles
        )

        if not story_is_stale and story_outputs_ready and not missing_child_summaries:
            metrics["stable_stories_skipped"] += 1
            return changed

        # 1. Process Story-level Summaries
        if article_count >= 2:
            if not story.summary:
                summary = summarize_story(story)
                if summary:
                    story.summary = summary
                    story.summary_generated_at = datetime.utcnow()
                    metrics["story_summaries_generated"] += 1
                    changed = True
                    logger.info(f"  [Processor] Story summary: {story.title[:60]}")

            if not story.deep_report:
                report = generate_deep_report(story)
                if report:
                    story.deep_report = report
                    metrics["deep_reports_generated"] += 1
                    changed = True
                    logger.info(f"  [Processor] Deep report: {story.title[:60]}")
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
        # This is critical for the static site links
        for article in story.articles:
            if not article.summary and article.content:
                summary = summarize_article(article)
                if summary:
                    article.summary = summary
                    metrics["child_article_summaries_generated"] += 1
                    changed = True
                    logger.info(f"    [Processor] Child article summary: {article.title[:60]}")
        db.session.commit()

        # Optionally generate per-article deep analysis for qualifying articles
        # (politics/science/business). Off by default because these prompts run
        # at a higher timeout than summaries and cost real extra GPU time per
        # article -- see _auto_article_deep_analysis_enabled(). Mirrors the child
        # summary block above: pre-filter cheaply with article_needs_deep_analysis
        # so we don't probe the LLM for articles that would never qualify.
        if _auto_article_deep_analysis_enabled():
            for article in story.articles:
                if (
                    not article.deep_analysis
                    and article.content
                    and article_needs_deep_analysis(article)
                ):
                    analysis = generate_article_deep_analysis(article)
                    if analysis:
                        article.deep_analysis = analysis
                        metrics["child_article_analyses_generated"] += 1
                        changed = True
                        logger.info(f"    [Processor] Child article deep analysis: {article.title[:60]}")
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

    ollama_available = check_ollama_status()
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


def sync_allsides_ratings():
    """
    Sync all outlets against the AllSides lookup table.
    - Upgrades Ollama-rated outlets to AllSides ratings where a match exists
    - Updates outlets whose AllSides score has changed since last sync
    - Propagates any score changes to all articles for that outlet
    Run monthly via scheduler, or manually via admin menu.
    """
    from news_fetcher.allsides_lookup import get_allsides_score
    from aggregator.models import Outlet

    logger.info("=== AllSides sync starting ===")

    outlets = Outlet.query.all()
    updated = 0
    skipped = 0

    for outlet in outlets:
        as_score = get_allsides_score(outlet.name)

        if as_score is None:
            skipped += 1
            continue

        score_changed = outlet.allsides_bias_score != as_score
        not_yet_allsides = outlet.bias_source != "allsides"

        if score_changed or not_yet_allsides:
            old_score = outlet.bias_score
            outlet.bias_score = as_score
            outlet.allsides_bias_score = as_score
            outlet.bias_source = "allsides"
            outlet.bias_retry_count = 0

            for article in outlet.articles:
                article.bias_score = as_score

            logger.info(
                f"  [AllSides Sync] {outlet.name}: "
                f"{old_score} -> {as_score} "
                f"({'upgraded from AI' if not_yet_allsides else 'score updated'})"
            )
            updated += 1

    db.session.commit()
    logger.info(f"=== AllSides sync complete. Updated {updated}, no match for {skipped} outlets. ===")


def publish_edition():
    """
    Create an Edition record for the current fetch cycle.
    Determines edition type (morning: 5am-4:59pm, evening: 5pm-4:59am) from Eastern time.
    Only includes stories that are new since the last edition, or have received
    new articles since then. Prefers multi-article stories and only falls back to
    single-article stories when needed to fill the edition.
    Skips if this edition slot already exists.
    """
    from zoneinfo import ZoneInfo
    from aggregator.models import Edition, Story, EditionStory

    eastern = ZoneInfo('America/New_York')
    now_eastern = datetime.now(eastern)
    hour = now_eastern.hour
    today = now_eastern.date()

    edition_type = 'morning' if 5 <= hour < 17 else 'evening'

    # Skip if this edition slot already published
    existing = Edition.query.filter_by(date=today, edition_type=edition_type).first()
    if existing:
        logger.info(f"[Edition] {edition_type} edition for {today} already published, skipping.")
        return {
            "status": "skipped_existing",
            "edition_id": existing.id,
            "date": str(today),
            "edition_type": edition_type,
            "story_count": existing.edition_stories.count(),
        }

    # Only suppress repeats from the immediately previous edition. Older
    # stories may return if they remain important in a later news cycle.
    prev_edition = Edition.query.filter(
        Edition.published == True
    ).order_by(Edition.created_at.desc()).first()
    prev_published_at = prev_edition.created_at if prev_edition else None
    prev_story_ids = set()
    if prev_edition:
        prev_story_ids = {es.story_id for es in prev_edition.edition_stories.all()}

    # Get top scored stories as candidates. Pull extra depth so suppressing
    # unchanged previous-edition stories does not leave the edition short.
    story_cutoff = datetime.utcnow() - timedelta(days=3)
    candidates = Story.query.filter(
        Story.headline_score > 0,
        Story.created_at >= story_cutoff
    ).order_by(Story.headline_score.desc()).limit(100).all()

    # Fallback for deployments running without the private headline-ranking
    # plugin: nothing in the open-source code assigns Story.headline_score, so
    # every story stays at 0 and the score gate above returns no candidates --
    # which leaves the edition (and therefore all automatic summary/deep-report
    # generation, which only runs over the latest edition) empty forever. When
    # that happens, fall back to recent stories ordered by recency and let the
    # multi-article preference and bias balancing below rank them, so the
    # open-source app can still publish editions on its own. When the ranking
    # plugin IS active, at least one story scores > 0 and this branch is skipped,
    # leaving the scored behavior unchanged.
    if not candidates:
        candidates = Story.query.filter(
            Story.created_at >= story_cutoff
        ).order_by(Story.created_at.desc()).limit(100).all()
        if candidates:
            logger.info(
                "[Edition] No stories have a positive headline_score "
                "(headline-ranking plugin inactive); falling back to %d recent "
                "stories ordered by recency.",
                len(candidates),
            )

    # Exclude stories with no scraped content on any article, and
    # single-article stories with generic roundup titles.
    filtered_candidates = []
    for story in candidates:
        first_article = _first_story_article(story)
        if first_article is None:
            logger.warning(
                "[Edition] Skipping story %s with no articles attached.",
                story.id,
            )
            continue

        from news_fetcher.summarizer import strip_html
        has_readable_content = any(
            len(strip_html(a.content or "").strip()) >= 200 for a in story.articles
        )
        if not has_readable_content:
            logger.warning(
                "[Edition] Skipping story %s — no article has readable content (%s article(s)).",
                story.id, len(story.articles),
            )
            continue

        if len(story.articles) == 1 and (
            is_generic_roundup_title(story.title) or
            is_generic_roundup_title(first_article.title)
        ):
            continue

        filtered_candidates.append(story)

    candidates = filtered_candidates

    eligible_multi = []
    eligible_single = []
    seen_story_ids = set()

    for story in candidates:
        if story.id in seen_story_ids:
            continue
        seen_story_ids.add(story.id)
        target_eligible = eligible_multi if len(story.articles) >= 2 else eligible_single

        if story.id not in prev_story_ids:
            # New story not in previous edition
            target_eligible.append((story, False))
        elif prev_published_at:
            # Story was in previous edition — only include if new articles arrived
            new_articles = [
                a for a in story.articles
                if a.fetched_at and a.fetched_at > prev_published_at
            ]
            if new_articles:
                target_eligible.append((story, True))

    eligible = eligible_multi + eligible_single

    # Final dedup safety net — ensures no story_id appears twice
    # regardless of how eligible was built
    seen = set()
    deduped = []
    for story, has_updates in eligible:
        if story.id not in seen:
            seen.add(story.id)
            deduped.append((story, has_updates))
    top_20 = []
    dedupe_skip_count = 0
    constrained_skip_counts = {
        "bias_cap": 0,
        "outlet_cap": 0,
    }
    balance_bucket_counts = {
        "leftish": 0,
        "center": 0,
        "rightish": 0,
        "unrated": 0,
    }
    mixed_coverage_count = 0
    outlet_story_counts = {}
    max_stories_per_balance_bucket = 8
    max_stories_per_primary_outlet = 4
    target_mixed_coverage_stories = 8
    target_minimums = {
        "leftish": 7,
        "center": 7,
        "rightish": 5,
    }
    available_by_bucket = {
        "leftish": 0,
        "center": 0,
        "rightish": 0,
        "unrated": 0,
    }
    for story, _ in deduped:
        available_by_bucket[_story_balance_bucket(story)] += 1

    def can_add_balanced(story):
        balance_bucket = _story_balance_bucket(story)
        primary_outlet = _story_primary_outlet(story)

        if balance_bucket_counts.get(balance_bucket, 0) >= max_stories_per_balance_bucket:
            constrained_skip_counts["bias_cap"] += 1
            return False
        if outlet_story_counts.get(primary_outlet, 0) >= max_stories_per_primary_outlet:
            constrained_skip_counts["outlet_cap"] += 1
            return False
        return True

    def add_story(story, has_updates):
        nonlocal mixed_coverage_count
        balance_bucket = _story_balance_bucket(story)
        primary_outlet = _story_primary_outlet(story)
        balance_bucket_counts[balance_bucket] = balance_bucket_counts.get(balance_bucket, 0) + 1
        outlet_story_counts[primary_outlet] = outlet_story_counts.get(primary_outlet, 0) + 1
        if _story_has_left_and_right_coverage(story):
            mixed_coverage_count += 1
        top_20.append((story, has_updates))

    # First reserve room for high-ranked stories that already have both
    # leftish and rightish coverage, then fill major balance buckets.
    selected_ids = set()
    for story, has_updates in deduped:
        if len(top_20) >= 20:
            break
        if not _story_has_left_and_right_coverage(story):
            continue
        if any(stories_look_duplicate_for_edition(story, kept_story) for kept_story, _ in top_20):
            dedupe_skip_count += 1
            continue
        if not can_add_balanced(story):
            continue
        add_story(story, has_updates)
        selected_ids.add(story.id)
        if mixed_coverage_count >= target_mixed_coverage_stories:
            break

    for bucket in ("rightish", "center", "leftish"):
        if len(top_20) >= 20:
            break
        target = min(target_minimums[bucket], available_by_bucket.get(bucket, 0))
        if target <= 0:
            continue
        for story, has_updates in deduped:
            if len(top_20) >= 20:
                break
            if story.id in selected_ids:
                continue
            if _story_balance_bucket(story) != bucket:
                continue
            if any(stories_look_duplicate_for_edition(story, kept_story) for kept_story, _ in top_20):
                dedupe_skip_count += 1
                continue
            if not can_add_balanced(story):
                continue
            add_story(story, has_updates)
            selected_ids.add(story.id)
            if balance_bucket_counts.get(bucket, 0) >= target or len(top_20) >= 20:
                break

    for story, has_updates in deduped:
        if len(top_20) >= 20:
            break
        if story.id in selected_ids:
            continue
        if any(stories_look_duplicate_for_edition(story, kept_story) for kept_story, _ in top_20):
            dedupe_skip_count += 1
            logger.info(
                "[Edition] Skipping same-event duplicate candidate: %s",
                story.title[:100],
            )
            continue
        if not can_add_balanced(story):
            continue
        add_story(story, has_updates)
        if len(top_20) >= 20:
            break

    # If caps left us short, fill remaining slots from same deduped list
    # while still preserving same-event dedupe and hard outlet caps. Bias caps
    # are relaxed only when every remaining candidate would breach them.
    if len(top_20) < 20:
        selected_ids = {story.id for story, _ in top_20}
        for relax_bias_cap in (False, True):
            for story, has_updates in deduped:
                if len(top_20) >= 20:
                    break
                if story.id in selected_ids:
                    continue
                if any(stories_look_duplicate_for_edition(story, kept_story) for kept_story, _ in top_20):
                    continue

                balance_bucket = _story_balance_bucket(story)
                primary_outlet = _story_primary_outlet(story)
                if outlet_story_counts.get(primary_outlet, 0) >= max_stories_per_primary_outlet:
                    constrained_skip_counts["outlet_cap"] += 1
                    continue
                if (
                    not relax_bias_cap and
                    balance_bucket_counts.get(balance_bucket, 0) >= max_stories_per_balance_bucket
                ):
                    constrained_skip_counts["bias_cap"] += 1
                    continue

                add_story(story, has_updates)
                selected_ids.add(story.id)
            if len(top_20) >= 20:
                break

    if len(top_20) > 20:
        logger.warning(
            "[Edition] Selection produced %s stories; trimming to 20.",
            len(top_20),
        )
        top_20 = top_20[:20]

    if not top_20:
        logger.warning(f"[Edition] No stories available for {edition_type} edition on {today}.")
        return {
            "status": "empty",
            "date": str(today),
            "edition_type": edition_type,
            "story_count": 0,
        }

    edition = Edition(date=today, edition_type=edition_type)
    db.session.add(edition)
    db.session.flush()

    # Selection above balances political/outlet diversity, which can pick a
    # lower-scored story before a higher-scored one. Display order should
    # still reflect importance, so sort by headline_score after selection.
    top_20.sort(key=lambda pair: pair[0].headline_score or 0, reverse=True)

    updated_repeat_count = 0
    carryover_count = 0
    for rank, (story, has_updates) in enumerate(top_20, 1):
        if has_updates:
            updated_repeat_count += 1
        elif story.id in prev_story_ids:
            carryover_count += 1
        es = EditionStory(
            edition_id=edition.id,
            story_id=story.id,
            rank=rank,
            headline_score_at_publish=story.headline_score,
            has_updates=has_updates,
        )
        db.session.add(es)

    db.session.commit()
    logger.info(
        f"[Edition] Published {edition_type} edition for {today} "
        f"with {len(top_20)} stories ({carryover_count} unchanged carry-overs, "
        f"{updated_repeat_count} repeated stories with new updates, "
        f"{dedupe_skip_count} same-event candidates skipped, "
        f"caps_skipped bias={constrained_skip_counts['bias_cap']} outlet={constrained_skip_counts['outlet_cap']}, "
        f"mixed_coverage={mixed_coverage_count}, balance={balance_bucket_counts})."
    )
    return {
        "status": "published",
        "edition_id": edition.id,
        "date": str(today),
        "edition_type": edition_type,
        "story_count": len(top_20),
        "carryover_count": carryover_count,
        "updated_repeat_count": updated_repeat_count,
        "dedupe_skip_count": dedupe_skip_count,
        "mixed_coverage_count": mixed_coverage_count,
        "balance_bucket_counts": balance_bucket_counts,
        "caps_skipped_bias": constrained_skip_counts["bias_cap"],
        "caps_skipped_outlet": constrained_skip_counts["outlet_cap"],
    }


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        fetch_and_store_articles("US Politics")
