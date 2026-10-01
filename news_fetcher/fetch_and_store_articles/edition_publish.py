# news_fetcher/fetch_and_store_articles/edition_publish.py
"""
Edition selection: publish_edition() and the same-event dedupe and
left/center/right balance helpers it ranks candidates with.
"""

import logging
from datetime import datetime, timedelta
from aggregator import db
from aggregator.article_signals import ROUNDUP_TITLE_PATTERNS, bias_bucket_for_score
from news_fetcher.story_grouper import normalize_title_tokens, titles_are_near_duplicates

logger = logging.getLogger(__name__)


def is_generic_roundup_title(title):
    normalized = (title or "").strip()
    return any(pattern.search(normalized) for pattern in ROUNDUP_TITLE_PATTERNS)


def guess_story_title(title):
    if ":" in title:
        return title.split(":")[0]
    if "-" in title:
        return title.split("-")[0]
    return " ".join(title.split()[:6])


def _strip_outlet_suffix(title, outlet_names):
    """Drop a trailing " - CNBC" / " | Reuters" naming one of the story's own
    outlets, so the outlet name doesn't count as a shared title word."""
    for sep in (" - ", " | ", " — ", " – "):
        head, found, tail = title.rpartition(sep)
        if found and head and tail.strip().lower() in outlet_names:
            return head
    return title


def _story_dedupe_titles(story, max_article_titles=5):
    outlet_names = set()
    for article in story.articles:
        source = getattr(article, "source", None)
        outlet = getattr(article, "outlet", None)
        if source:
            outlet_names.add(source.strip().lower())
        if outlet is not None and getattr(outlet, "name", None):
            outlet_names.add(outlet.name.strip().lower())

    titles = []
    if story.title:
        titles.append(story.title)
    if story.headline and story.headline != story.title:
        titles.append(story.headline)
    for article in story.articles[:max_article_titles]:
        if article.title:
            titles.append(article.title)
    return [_strip_outlet_suffix(title, outlet_names) for title in titles]


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

# Added 2026-10-01: edition 475 dropped "A'ja Wilson receives technical foul"
# as a duplicate of a Schumer/Cornell story on was/what/who, and a $200bn
# South Korea energy story as a duplicate of an Iowa steel mill on
# announce/announces/trump/watch. Function words and stock event words carry
# no event identity.
EDITION_DEDUPE_GENERIC_TOKENS |= {
    # function words TITLE_STOPWORDS (grouping) deliberately leaves in
    "about", "all", "also", "any", "are", "been", "being", "but", "can",
    "did", "does", "get", "gets", "got", "had", "has", "have", "his", "how",
    "its", "may", "most", "not", "now", "one", "our", "she", "than", "that",
    "their", "them", "then", "they", "this", "two", "very", "was", "were",
    "what", "when", "who", "why", "will", "you", "your",
    # stock news and event vocabulary
    "announce", "announced", "announces", "day", "first", "game", "games",
    "loss", "losses", "report", "reports", "season", "today", "update",
    "updates", "watch", "week", "wins", "year", "years",
    # outlet/site fragments and filler that survive the suffix strip
    "news", "com", "top", "again", "during", "down", "off", "man", "nears",
    "plan", "order", "orders", "coach",
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
    logged_duplicate_ids = set()

    def is_duplicate_of_kept(story):
        """Same-event check against the stories kept so far. Logs each skipped
        story once, with the kept story it matched, so a wrong skip can be
        audited (previously only the last fill loop logged, without the match)."""
        for kept_story, _ in top_20:
            if stories_look_duplicate_for_edition(story, kept_story):
                if story.id not in logged_duplicate_ids:
                    logged_duplicate_ids.add(story.id)
                    logger.info(
                        "[Edition] Skipping same-event duplicate candidate %s '%s' -- matches kept %s '%s'",
                        story.id, (story.headline or story.title or "")[:90],
                        kept_story.id, (kept_story.headline or kept_story.title or "")[:90],
                    )
                return True
        return False
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
        if is_duplicate_of_kept(story):
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
            if is_duplicate_of_kept(story):
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
        if is_duplicate_of_kept(story):
            dedupe_skip_count += 1
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
                if is_duplicate_of_kept(story):
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
