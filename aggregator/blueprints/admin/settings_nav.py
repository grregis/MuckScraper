"""The Settings section list (templates/settings_base.html).

Grouped as in the settings mock (Philip, 2026-10-22): General, Sources, AI
and Pipeline, each link with a count where one helps. A page names its own
entry by key; everything else is built here so the order and labels live in
one place.
"""
from flask import url_for

from aggregator.models import User, Topic, RssFeed, PromptTemplate, ScheduledFetch

from . import admin

# (group, [(key, label, endpoint, count_model or None, is_new)])
SETTINGS_SECTIONS = [
    ("General", [
        ("overview", "Overview", "admin.tools_page", None, False),
        ("users", "Users", "admin.users_page", User, False),
    ]),
    ("Sources", [
        ("topics", "Topics", "admin.topics_page", Topic, False),
        ("rss", "RSS feeds", "admin.rss_feeds_page", RssFeed, False),
        ("fetches", "Scheduled fetches", "admin.scheduled_fetches_page", ScheduledFetch, False),
        ("ingest", "Ingestion blocklist", "admin.ingestion_blocks_page", None, False),
        ("scrapeblock", "Scrape blocklist", "admin.scrape_blocklist", None, False),
    ]),
    ("AI", [
        ("llm", "LLM providers", "admin.llm_providers_page", None, True),
        ("prompts", "Prompts", "admin.prompts_page", PromptTemplate, False),
    ]),
    ("Pipeline", [
        ("schedule", "Run schedule", "admin.pipeline_schedule_page", None, False),
    ]),
]


@admin.app_template_global()
def settings_sections(active=None):
    """The section list for the Settings nav, with `active` marked current."""
    groups = []
    for name, items in SETTINGS_SECTIONS:
        links = []
        for key, label, endpoint, model, is_new in items:
            links.append({
                "key": key,
                "label": label,
                "href": url_for(endpoint),
                "count": model.query.count() if model is not None else None,
                "is_new": is_new,
                "current": key == active,
            })
        groups.append({"name": name, "items": links})
    return groups
