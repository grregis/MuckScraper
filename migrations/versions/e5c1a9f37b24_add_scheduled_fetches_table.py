"""add scheduled_fetches table

Revision ID: e5c1a9f37b24
Revises: c3f8b2d40a17
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa

revision = 'e5c1a9f37b24'
down_revision = 'c3f8b2d40a17'
branch_labels = None
depends_on = None

# Verbatim copy of news_fetcher/scheduler.py's SCHEDULED_FETCHES list as it
# stood immediately before this table replaced it, so an existing install
# keeps fetching exactly what it fetched yesterday. Kept inline rather than
# imported: a migration has to stay runnable after the source list is gone.
# The descriptions come from admin.py's FETCH_PRESETS, a second hardcoded copy
# of the same five configs that this table also replaces.
SEED_ENTRIES = [
    {
        "label":            "US Politics",
        "description":      "Congress, White House, courts, elections",
        "mode":             "query",
        "newsapi_country":  None,
        "newsapi_category": None,
        "newsapi_query":    "US politics congress white house senate supreme court",
        "gnews_query":      "US politics congress white house",
        "gnews_category":   None,
    },
    {
        "label":            "Business & Economy",
        "description":      "Top business headlines",
        "mode":             "top",
        "newsapi_country":  "us",
        "newsapi_category": "business",
        "newsapi_query":    None,
        "gnews_query":      None,
        "gnews_category":   "business",
    },
    {
        "label":            "Science & Health",
        "description":      "Research, medicine, technology",
        "mode":             "query",
        "newsapi_country":  None,
        "newsapi_category": None,
        "newsapi_query":    "scientific breakthroughs medical research healthcare tech",
        "gnews_query":      "science health research",
        "gnews_category":   "science",
    },
    {
        "label":            "Sports",
        "description":      "Top sports headlines",
        "mode":             "top",
        "newsapi_country":  "us",
        "newsapi_category": "sports",
        "newsapi_query":    None,
        "gnews_query":      None,
        "gnews_category":   "sports",
    },
    {
        "label":            "World News",
        "description":      "International news, conflict, diplomacy",
        "mode":             "query",
        "newsapi_country":  None,
        "newsapi_category": None,
        "newsapi_query":    "international world global news conflicts diplomacy",
        "gnews_query":      "world global news",
        "gnews_category":   "world",
    },
]


def upgrade():
    scheduled_fetches_table = op.create_table(
        "scheduled_fetches",
        sa.Column("id",               sa.Integer(),          nullable=False),
        sa.Column("label",            sa.String(),           nullable=False),
        sa.Column("description",      sa.String(),           nullable=True),
        sa.Column("mode",             sa.String(length=16),  nullable=False, server_default="query"),
        sa.Column("newsapi_country",  sa.String(length=8),   nullable=True),
        sa.Column("newsapi_category", sa.String(length=32),  nullable=True),
        sa.Column("newsapi_query",    sa.String(),           nullable=True),
        sa.Column("gnews_query",      sa.String(),           nullable=True),
        sa.Column("gnews_category",   sa.String(length=32),  nullable=True),
        sa.Column("sort_order",       sa.Integer(),          nullable=False, server_default="0"),
        sa.Column("is_active",        sa.Boolean(),          nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("label", name="uq_scheduled_fetch_label"),
    )

    rows = [
        {**entry, "sort_order": index, "is_active": True}
        for index, entry in enumerate(SEED_ENTRIES)
    ]
    op.bulk_insert(scheduled_fetches_table, rows)


def downgrade():
    op.drop_table("scheduled_fetches")
