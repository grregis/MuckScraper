from datetime import datetime as dt

from aggregator.article_signals import is_independent_source
from aggregator.constants import AGGREGATORS


def apply_aggregator_filter(story):
    originals = []
    aggregators = []
    has_good_original = False
    seen_articles = set()
    sorted_articles = sorted(story.articles, key=lambda x: x.date or dt.min, reverse=True)
    for art in sorted_articles:
        key = (art.title, art.outlet_id)
        if key in seen_articles:
            continue
        seen_articles.add(key)
        outlet_name = art.outlet.name if art.outlet else ""
        if any(agg in outlet_name for agg in AGGREGATORS):
            aggregators.append(art)
        else:
            originals.append(art)
            if art.content and len(art.content) > 500:
                has_good_original = True
    story.display_articles = originals if has_good_original else (originals + aggregators)
    if not has_good_original:
        story.display_articles.sort(key=lambda x: x.date or dt.min, reverse=True)

    # Collect unique outlets for display. Only outlets that contributed real
    # scraped content count -- a blocked paywall or a 112-char RSS stub carries
    # the outlet's name but none of its reporting, so counting it would claim
    # corroboration the story does not have. See
    # article_signals.INDEPENDENT_CONTENT_FLOOR.
    #
    # An outlet is not disqualified by one thin article: a later article from
    # the same outlet that clears the floor still counts, since outlet_id is
    # only marked as seen once accepted.
    unique_outlets = []
    seen_outlet_ids = set()
    for art in story.display_articles:
        if not art.outlet_id or art.outlet_id in seen_outlet_ids:
            continue
        if not is_independent_source(art):
            continue
        unique_outlets.append(art.outlet)
        seen_outlet_ids.add(art.outlet_id)

    # Never render zero outlets. If nothing clears the floor, fall back to the
    # best-scraped article's outlet so the story still attributes a source --
    # the count is then honest about there being exactly one.
    if not unique_outlets:
        best = max(
            (art for art in story.display_articles if art.outlet_id and art.outlet),
            key=lambda art: len(art.content or ""),
            default=None,
        )
        if best is not None:
            unique_outlets = [best.outlet]

    story.unique_outlets = unique_outlets

    status_counts = {
        "success": 0,
        "fallback": 0,
        "blocked": 0,
    }
    for article in story.display_articles:
        status = (article.scrape_status or "blocked").lower()
        if status == "success":
            status_counts["success"] += 1
        elif status == "fallback":
            status_counts["fallback"] += 1
        else:
            status_counts["blocked"] += 1

    total_articles = len(story.display_articles)
    readable_articles = status_counts["success"] + status_counts["fallback"]
    story.scrape_quality = {
        "total": total_articles,
        "success": status_counts["success"],
        "fallback": status_counts["fallback"],
        "blocked": status_counts["blocked"],
        "readable_pct": round((readable_articles / total_articles) * 100) if total_articles else 0,
        "full_pct": round((status_counts["success"] / total_articles) * 100) if total_articles else 0,
    }


def annotate_edition_story_flags(edition_story_rows):
    """Flatten EditionStory rows to their Story objects, tagged with .edition_has_updates."""
    stories = []
    for edition_story in edition_story_rows:
        story = edition_story.story
        story.edition_has_updates = bool(getattr(edition_story, "has_updates", False))
        stories.append(story)
    return stories
