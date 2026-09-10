# news_fetcher/fetch_and_store_articles/outlets.py
"""
Outlet identity and bias: source-name normalization, duplicate-outlet
merging, and the AllSides / LLM bias retry and sync passes.
"""

import logging
from urllib.parse import urlparse
from aggregator import db
from aggregator.models import Outlet
from news_fetcher.allsides_lookup import get_allsides_score
from news_fetcher.outlet_bias_llm import get_outlet_bias_from_llm

logger = logging.getLogger(__name__)


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
