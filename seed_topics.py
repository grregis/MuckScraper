"""Idempotent seeder for the German (DE) MuckScraper setup.

Seeds the canonical DE topics (with full fetch configuration) and a set of
German RSS feeds into the database. Safe to re-run: existing rows are updated
in place, missing rows are inserted, and stale US fetch-topics that carry a
fetch_mode are retired (is_active=False). Classification-only topics created
by the LLM classifier (no fetch_mode) are left untouched so historical tags
keep working.

Run via:
    docker compose exec app python seed_topics.py
or automatically from bootstrap_admin.py on a fresh install.

This is the DE equivalent of what the upstream migration a3f7c2e9d1b8 did for
the US topic set, but moved out of a migration so the admin can re-run it
(the /topics/reseed button) and edit topics afterwards via the admin UI.
"""
import logging

from aggregator import db
from aggregator.app import app
from aggregator.models import Topic, RssFeed

logger = logging.getLogger("seed_topics")


# Canonical DE topics. `name` is the unique key and doubles as the label shown
# in the admin fetch page. fetch_mode None => classification-only (no scheduled
# fetch, still usable as a tag).
DE_TOPICS = [
    {
        "name": "DE Politik", "icon": "DP", "sort_order": 0,
        "description": "Bundestag, Regierung, Kanzler, Parteien",
        "fetch_mode": "query", "fetch_country": "de", "fetch_category": "general",
        "fetch_query": "Bundestag OR Regierung OR Kanzler OR Parteien",
        "gnews_query": "Bundestag Regierung Kanzler", "gnews_category": "nation",
    },
    {
        "name": "DE Inland", "icon": "DN", "sort_order": 1,
        "description": "Top-Schlagzeilen aus Deutschland",
        "fetch_mode": "top", "fetch_country": "de", "fetch_category": "general",
        "fetch_query": None, "gnews_query": None, "gnews_category": "nation",
    },
    {
        "name": "Welt", "icon": "IN", "sort_order": 2,
        "description": "Internationale Nachrichten",
        "fetch_mode": "top", "fetch_country": None, "fetch_category": "general",
        "fetch_query": None, "gnews_query": None, "gnews_category": "world",
    },
    {
        "name": "Wirtschaft & Finanzen", "icon": "WF", "sort_order": 3,
        "description": "Börse, Unternehmen, Konjunktur",
        "fetch_mode": "top", "fetch_country": "de", "fetch_category": "business",
        "fetch_query": None, "gnews_query": None, "gnews_category": "business",
    },
    {
        "name": "Wissenschaft & Gesundheit", "icon": "WG", "sort_order": 4,
        "description": "Forschung, Medizin, Gesundheit",
        "fetch_mode": "top", "fetch_country": "de", "fetch_category": "science",
        "fetch_query": None, "gnews_query": None, "gnews_category": "science",
    },
    {
        "name": "Technologie & KI", "icon": "TC", "sort_order": 5,
        "description": "Tech, Künstliche Intelligenz, Digital",
        "fetch_mode": "query", "fetch_country": "de", "fetch_category": "technology",
        "fetch_query": "Technologie OR KI OR \"Künstliche Intelligenz\"",
        "gnews_query": "Technologie KI", "gnews_category": "technology",
    },
    {
        "name": "Sport", "icon": "SP", "sort_order": 6,
        "description": "Top-Sport aus Deutschland",
        "fetch_mode": "top", "fetch_country": "de", "fetch_category": "sports",
        "fetch_query": None, "gnews_query": None, "gnews_category": "sports",
    },
    {
        "name": "Kultur", "icon": "KU", "sort_order": 7,
        "description": "Literatur, Film, Musik, Bühne",
        "fetch_mode": "query", "fetch_country": "de", "fetch_category": "general",
        "fetch_query": "Kultur OR Literatur OR Film OR Musik",
        "gnews_query": "Kultur Literatur Film", "gnews_category": "nation",
    },
    {
        "name": "Sonstiges", "icon": "OT", "sort_order": 99,
        "description": "Fallback für alles, woanders nicht eingeordnet",
        "fetch_mode": None, "fetch_country": None, "fetch_category": None,
        "fetch_query": None, "gnews_query": None, "gnews_category": None,
    },
]


# German RSS feeds. All go into the "general" bucket; the admin can move them
# to right_enrichment / left_enrichment later. URLs are best-known defaults;
# verify/adjust via /admin/rss-feeds if a feed rejects.
DE_RSS_FEEDS = [
    {"url": "https://www.tagesschau.de/xml/rss2/",                "bucket": "general", "label": "Tagesschau"},
    {"url": "https://www.zdfheute.de/index.rss",                  "bucket": "general", "label": "ZDFheute"},
    {"url": "https://www.spiegel.de/schlagzeilen/tops/index.rss", "bucket": "general", "label": "Spiegel Schlagzeilen"},
    {"url": "https://newsfeed.zeit.de/index",                     "bucket": "general", "label": "Zeit Online"},
    {"url": "https://www.faz.net/rss/aktuell/",                   "bucket": "general", "label": "FAZ Aktuell"},
    {"url": "https://www.heise.de/rss/heise-top.xml",             "bucket": "general", "label": "Heise Top"},
    {"url": "https://www.golem.de/rss.php",                       "bucket": "general", "label": "Golem"},
    {"url": "https://taz.de/rss.xml",                             "bucket": "general", "label": "taz"},
    {"url": "https://hnrss.org/frontpage",                        "bucket": "general", "label": "Hacker News"},
]

# Fields that seed_topics owns and re-applies on every run. Other fields
# (description, prompts) are only filled if empty, so manual admin edits win.
_TOPIC_OWNED_FIELDS = (
    "icon", "sort_order", "fetch_mode", "fetch_country", "fetch_category",
    "fetch_query", "gnews_query", "gnews_category",
)
_TOPIC_FILL_IF_EMPTY = ("description",)


def seed_topics():
    canonical_names = {spec["name"] for spec in DE_TOPICS}
    created = updated = 0

    for spec in DE_TOPICS:
        topic = Topic.query.filter_by(name=spec["name"]).first()
        if topic is None:
            topic = Topic(name=spec["name"])
            db.session.add(topic)
            for field in _TOPIC_OWNED_FIELDS:
                setattr(topic, field, spec.get(field))
            for field in _TOPIC_FILL_IF_EMPTY:
                setattr(topic, field, spec.get(field))
            topic.is_active = True
            created += 1
        else:
            for field in _TOPIC_OWNED_FIELDS:
                setattr(topic, field, spec.get(field))
            for field in _TOPIC_FILL_IF_EMPTY:
                if not getattr(topic, field, None):
                    setattr(topic, field, spec.get(field))
            # Re-activate canonical topics that were retired earlier.
            topic.is_active = True
            updated += 1

    # Retire stale fetch-topics (old US presets like "US Politics") that are
    # not part of the DE canonical set. Classification-only LLM topics
    # (fetch_mode is None) are left alone so historical tags survive.
    retired = 0
    for topic in Topic.query.all():
        if topic.name in canonical_names:
            continue
        if topic.fetch_mode is not None and topic.is_active:
            topic.is_active = False
            retired += 1

    db.session.commit()
    logger.info(
        "seed_topics: %d created, %d updated, %d retired (stale fetch-topics).",
        created, updated, retired,
    )
    return {"created": created, "updated": updated, "retired": retired}


def seed_rss_feeds():
    added = 0
    for spec in DE_RSS_FEEDS:
        existing = RssFeed.query.filter_by(url=spec["url"], bucket=spec["bucket"]).first()
        if existing:
            # Keep label in sync if the feed was added manually without one.
            if not existing.label and spec.get("label"):
                existing.label = spec["label"]
            continue
        db.session.add(RssFeed(
            url=spec["url"], bucket=spec["bucket"],
            label=spec.get("label"), enabled=True,
        ))
        added += 1
    db.session.commit()
    logger.info("seed_rss_feeds: %d added.", added)
    return {"added": added}


def run():
    with app.app_context():
        topics = seed_topics()
        rss = seed_rss_feeds()
    return {"topics": topics, "rss": rss}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = run()
    print(result)