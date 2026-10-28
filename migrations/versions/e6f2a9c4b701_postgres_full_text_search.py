"""Postgres full-text search replaces Meilisearch.

Revision ID: e6f2a9c4b701
Revises: a9c4e2f7b1d5
Create Date: 2026-10-22

Adds a generated `search_vector` column with a GIN index to stories and
articles, and pg_trgm indexes on story titles/headlines and article titles for
typo-tolerant matching (aggregator/search.py). The expressions must match
STORY_SEARCH_VECTOR_SQL / ARTICLE_SEARCH_VECTOR_SQL in aggregator/models.py.

Adding a stored generated column rewrites the table, so on a large install
the duration depends on database size and hardware; queries wait on it.
"""
from alembic import op

revision = 'e6f2a9c4b701'
down_revision = 'a9c4e2f7b1d5'
branch_labels = None
depends_on = None

STORY_SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(headline, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(summary, '')), 'B') || "
    "setweight(to_tsvector('english'::regconfig, left(coalesce(deep_report, ''), 8000)), 'C')"
)
ARTICLE_SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(summary, '')), 'B') || "
    "setweight(to_tsvector('english'::regconfig, coalesce(source, '')), 'C') || "
    "setweight(to_tsvector('english'::regconfig, left(coalesce(content, ''), 6000)), 'D')"
)


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "ALTER TABLE stories ADD COLUMN IF NOT EXISTS search_vector tsvector "
        f"GENERATED ALWAYS AS ({STORY_SEARCH_VECTOR_SQL}) STORED"
    )
    op.execute(
        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS search_vector tsvector "
        f"GENERATED ALWAYS AS ({ARTICLE_SEARCH_VECTOR_SQL}) STORED"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_stories_search_vector ON stories USING gin (search_vector)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_articles_search_vector ON articles USING gin (search_vector)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_stories_title_trgm ON stories USING gin (title gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_stories_headline_trgm ON stories USING gin (headline gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_articles_title_trgm ON articles USING gin (title gin_trgm_ops)")
    # The Search reindex status row (admin Tools) no longer means anything.
    op.execute("DELETE FROM app_settings WHERE key = 'search_reindex_status_v1'")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_articles_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_stories_headline_trgm")
    op.execute("DROP INDEX IF EXISTS ix_stories_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_articles_search_vector")
    op.execute("DROP INDEX IF EXISTS ix_stories_search_vector")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS search_vector")
    op.execute("ALTER TABLE stories DROP COLUMN IF EXISTS search_vector")
