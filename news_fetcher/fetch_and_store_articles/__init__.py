# news_fetcher/fetch_and_store_articles/__init__.py
"""
Ingestion, grouping review, outlet bias, edition publishing and the bulk
repair actions -- split into submodules by concern (2026-09-10) from the
single 2,400-line module this package replaces.

Every public name the old module had is re-exported here, so
`from news_fetcher.fetch_and_store_articles import X` keeps working for the
scheduler, the admin blueprint, rss_fetcher, the helper scripts and the MCP
server. Import from the submodule directly when monkeypatching in tests:
a patch has to land where the name is *looked up*, not where it is
re-exported from.
"""

from aggregator import create_app, db  # noqa: F401 -- db re-exported for callers
from news_fetcher.summarizer import check_ollama_status  # noqa: F401

app = create_app()

from .ingestion import (  # noqa: F401
    BLOCK_KIND_SOURCE,
    BLOCK_KIND_TITLE,
    get_ingestion_blocks,
    GROUPING_LOOKBACK_DAYS,
    BIAS_BUCKETS,
    empty_store_metrics,
    merge_count_maps,
    serialize_grouping_candidate_ids,
    deserialize_grouping_candidate_ids,
    truncate_db_string,
    get_or_create_topic,
    normalize_url,
    detect_duplicate_outlet_content,
    store_articles,
)
from .providers import (  # noqa: F401
    fetch_newsapi,
    fetch_gnews,
    cleanup_old_payloads,
    fetch_and_store_articles,
)
from .outlets import (  # noqa: F401
    retry_unrated_outlets,
    DOMAIN_SOURCE_OVERRIDES,
    source_name_from_url,
    normalize_source_name,
    merge_duplicate_outlets,
    sync_allsides_ratings,
)
from .grouping_review import (  # noqa: F401
    clear_story_headline_after_article_departs,
    clear_stale_single_article_headlines,
    review_ambiguous_grouping_matches,
    regroup_ungrouped_stories,
)
from .edition_publish import (  # noqa: F401
    is_generic_roundup_title,
    guess_story_title,
    _story_dedupe_titles,
    _story_signature_tokens,
    EDITION_DEDUPE_GENERIC_TOKENS,
    _distinctive_shared_tokens,
    stories_look_duplicate_for_edition,
    _story_balance_bucket,
    _story_has_left_and_right_coverage,
    _story_primary_outlet,
    _first_story_article,
    publish_edition,
)
from .edition_content import (  # noqa: F401
    generate_missing_deep_reports,
    STALE_ARTICLE_THRESHOLD,
    BACKFILL_LOOKBACK_DAYS,
    AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY,
    _auto_article_deep_analysis_enabled,
    _fill_story_content,
    process_current_edition,
)
from .maintenance import (  # noqa: F401
    generate_missing_embeddings,
    audit_existing_scrapes,
    force_resummarize_all,
    force_regroup_all,
    reclassify_all_articles,
    ollama_catchup,
)

from . import ingestion, providers, outlets, grouping_review, edition_publish, edition_content, maintenance  # noqa: E402,F401
