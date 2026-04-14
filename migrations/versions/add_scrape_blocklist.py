"""add scrape_blocklist table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

PERMANENT_BLOCKLIST = [
    ("nytimes.com",        "Hard paywall — scraping permanently blocked"),
    ("wsj.com",            "Hard paywall — scraping permanently blocked"),
    ("ft.com",             "Hard paywall — scraping permanently blocked"),
    ("washingtonpost.com", "Hard paywall — scraping permanently blocked"),
    ("theathletic.com",    "Hard paywall — scraping permanently blocked"),
    ("bloomberg.com",      "Hard paywall — scraping permanently blocked"),
    ("thetimes.co.uk",     "Hard paywall — scraping permanently blocked"),
    ("economist.com",      "Hard paywall — scraping permanently blocked"),
    ("newyorker.com",      "Hard paywall — scraping permanently blocked"),
    ("foreignpolicy.com",  "Hard paywall — scraping permanently blocked"),
    ("hbr.org",            "Hard paywall — scraping permanently blocked"),
    ("seekingalpha.com",   "Hard paywall — scraping permanently blocked"),
    ("barrons.com",        "Hard paywall — scraping permanently blocked"),
]


def upgrade():
    blocklist_table = op.create_table(
        "scrape_blocklist",
        sa.Column("id",           sa.Integer(),  nullable=False),
        sa.Column("domain",       sa.String(),   nullable=False),
        sa.Column("reason",       sa.String(),   nullable=False),
        sa.Column("added_at",     sa.DateTime(), nullable=True),
        sa.Column("is_permanent", sa.Boolean(),  nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain"),
    )
    op.create_index("ix_scrape_blocklist_domain", "scrape_blocklist", ["domain"])

    op.bulk_insert(
        blocklist_table,
        [
            {
                "domain":       domain,
                "reason":       reason,
                "added_at":     datetime.utcnow(),
                "is_permanent": True,
            }
            for domain, reason in PERMANENT_BLOCKLIST
        ],
    )


def downgrade():
    op.drop_index("ix_scrape_blocklist_domain", table_name="scrape_blocklist")
    op.drop_table("scrape_blocklist")
