"""add hit threshold and expiry to scrape_blocklist

Revision ID: a4b7f0d92c31
Revises: f1a6d20c8e39
Create Date: 2026-09-12
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timedelta

revision = 'a4b7f0d92c31'
down_revision = 'f1a6d20c8e39'
branch_labels = None
depends_on = None

# fableaudit.md 1.1: the auto-blocklist was suppressing whole domains (Fox
# News, BBC, Toronto Sun, and ~10 others) for months on the strength of one
# bad scrape, with no way back except the manual Unblock button. This adds
# the two fixes Regis asked for: a minimum-hit threshold (news_fetcher.scraper
# .BLOCKLIST_MIN_HIT_THRESHOLD, currently 3 -- kept as a literal here, not
# imported, so this migration stays runnable after that constant changes)
# before a domain is actually treated as blocked, and a sliding expiry once
# it is. See the ScrapeBlocklist docstring in aggregator/models.py and
# add_to_blocklist()/is_domain_blocked() in news_fetcher/scraper.py for the
# runtime logic this schema supports.
#
# Existing non-permanent rows predate hit-counting, so there's no real hit
# history to backfill. Rather than leaving them blocked forever (the old
# behavior) or unblocking all ~20 of them at once on upgrade (which would
# hand the very next run a flood of paywall/login-wall content from every
# one of them simultaneously), grandfather them in at the threshold with a
# fresh expiry: they stay blocked for one more window, then need 3 real
# fresh hits like any newly-flagged domain. Permanent rows are untouched --
# is_permanent bypasses hit_count/expires_at entirely, both here and at
# runtime.
GRANDFATHER_WINDOW_HOURS = 48

# Found while writing this migration, unrelated to hit-counting: on at least
# this database, every one of the hard-paywall domains seeded as
# is_permanent=true by b2c3d4e5f6a7 (add_scrape_blocklist.py) now reads
# is_permanent=false -- their rows carry an auto-blocker `reason` and a later
# `added_at` than the seed migration, so something (an unblock/re-add cycle,
# a manual edit) replaced the seeded row rather than merely failing to insert
# it. Left alone, this migration's grandfathering above would hand a genuine,
# permanent hard-paywall domain like nytimes.com or wsj.com an ordinary
# 48-hour expiry -- wrong, since these will never stop paywalling and don't
# belong on the hit-counted path at all. Restored verbatim from
# b2c3d4e5f6a7's own seed list before grandfathering runs, so these are
# excluded from it and go back to blocking unconditionally.
PERMANENT_BLOCKLIST_DOMAINS = [
    "nytimes.com", "wsj.com", "ft.com", "washingtonpost.com", "theathletic.com",
    "bloomberg.com", "thetimes.co.uk", "economist.com", "newyorker.com",
    "foreignpolicy.com", "hbr.org", "seekingalpha.com", "barrons.com",
]


def upgrade():
    op.add_column(
        "scrape_blocklist",
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("scrape_blocklist", sa.Column("last_hit_at", sa.DateTime(), nullable=True))
    op.add_column("scrape_blocklist", sa.Column("expires_at", sa.DateTime(), nullable=True))

    conn = op.get_bind()
    now = datetime.utcnow()
    grandfather_expiry = now + timedelta(hours=GRANDFATHER_WINDOW_HOURS)

    conn.execute(sa.text("UPDATE scrape_blocklist SET last_hit_at = added_at"))

    conn.execute(
        sa.text(
            """
            UPDATE scrape_blocklist
            SET is_permanent = true, hit_count = 1, expires_at = NULL
            WHERE domain = ANY(:domains)
            """
        ),
        {"domains": PERMANENT_BLOCKLIST_DOMAINS},
    )

    conn.execute(
        sa.text(
            """
            UPDATE scrape_blocklist
            SET hit_count = 3, expires_at = :expiry
            WHERE is_permanent = false
            """
        ),
        {"expiry": grandfather_expiry},
    )


def downgrade():
    op.drop_column("scrape_blocklist", "expires_at")
    op.drop_column("scrape_blocklist", "last_hit_at")
    op.drop_column("scrape_blocklist", "hit_count")
