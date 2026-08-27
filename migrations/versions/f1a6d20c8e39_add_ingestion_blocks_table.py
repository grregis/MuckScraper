"""add ingestion_blocks table

Revision ID: f1a6d20c8e39
Revises: e5c1a9f37b24
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

revision = 'f1a6d20c8e39'
down_revision = 'e5c1a9f37b24'
branch_labels = None
depends_on = None

# Verbatim copy of news_fetcher/fetch_and_store_articles.py's BLOCKED_SOURCES
# and BLOCKED_TITLE_KEYWORDS as they stood immediately before this table
# replaced them, so an existing install blocks exactly what it blocked
# yesterday. Kept inline rather than imported: a migration has to stay
# runnable after the source lists are gone.
#
# The `note` on each row is new -- the old lists carried their rationale in
# block comments covering a whole group, which the table has nowhere to put.
# The long-form reasoning stays in TODO.md; these are the one-line versions
# that show in the admin UI.

SEED_SOURCES = [
    ('github.com', 'Developer/repo noise, not news'),
    ('github.blog', 'Developer/repo noise, not news'),
    ('dev.to', 'Developer/repo noise, not news'),
    ('stackoverflow.com', 'Developer/repo noise, not news'),
    ('reddit.com', 'Developer/repo noise, not news'),
    ('npmjs.com', 'Developer/repo noise, not news'),
    ('pypi.org', 'Developer/repo noise, not news'),
    ('actionnetwork.com', 'Sports-betting tipster site -- entire output is odds and picks'),
    ('vsin.com', 'Sports-betting tipster site -- entire output is odds and picks'),
    ('covers.com', 'Sports-betting tipster site -- entire output is odds and picks'),
    ('sportsline.com', 'Sports-betting tipster site -- entire output is odds and picks'),
    ('lineups.com', 'Sports-betting tipster site -- entire output is odds and picks'),
    ('nhl.com', 'Official league media -- PR, not journalism (substring also covers subdomains)'),
    ('mlb.com', 'Official league media -- PR, not journalism (substring also covers subdomains)'),
    ('nba.com', 'Official league media -- PR, not journalism (substring also covers subdomains)'),
    ('nfl.com', 'Official league media -- PR, not journalism (substring also covers subdomains)'),
    ('49ers.com', 'Official club media -- PR, not journalism'),
    ('atlantafalcons.com', 'Official club media -- PR, not journalism'),
    ('azcardinals.com', 'Official club media -- PR, not journalism'),
    ('baltimoreravens.com', 'Official club media -- PR, not journalism'),
    ('bengals.com', 'Official club media -- PR, not journalism'),
    ('buccaneers.com', 'Official club media -- PR, not journalism'),
    ('buffalobills.com', 'Official club media -- PR, not journalism'),
    ('chargers.com', 'Official club media -- PR, not journalism'),
    ('chicagobears.com', 'Official club media -- PR, not journalism'),
    ('chiefs.com', 'Official club media -- PR, not journalism'),
    ('clevelandbrowns.com', 'Official club media -- PR, not journalism'),
    ('commanders.com', 'Official club media -- PR, not journalism'),
    ('dallascowboys.com', 'Official club media -- PR, not journalism'),
    ('denverbroncos.com', 'Official club media -- PR, not journalism'),
    ('detroitlions.com', 'Official club media -- PR, not journalism'),
    ('giants.com', 'Official club media -- PR, not journalism'),
    ('jaguars.com', 'Official club media -- PR, not journalism'),
    ('miamidolphins.com', 'Official club media -- PR, not journalism'),
    ('neworleanssaints.com', 'Official club media -- PR, not journalism'),
    ('newyorkjets.com', 'Official club media -- PR, not journalism'),
    ('orlandomagic.com', 'Official club media -- PR, not journalism'),
    ('packers.com', 'Official club media -- PR, not journalism'),
    ('panthers.com', 'Official club media -- PR, not journalism'),
    ('patriots.com', 'Official club media -- PR, not journalism'),
    ('philadelphiaeagles.com', 'Official club media -- PR, not journalism'),
    ('pistons.com', 'Official club media -- PR, not journalism'),
    ('raiders.com', 'Official club media -- PR, not journalism'),
    ('seahawks.com', 'Official club media -- PR, not journalism'),
    ('steelers.com', 'Official club media -- PR, not journalism'),
    ('tennesseetitans.com', 'Official club media -- PR, not journalism'),
    ('therams.com', 'Official club media -- PR, not journalism'),
    ('timberwolves.com', 'Official club media -- PR, not journalism'),
    ('vikings.com', 'Official club media -- PR, not journalism'),
    ('businesswire.com', 'Press-release wire -- corporate announcements carried verbatim'),
    ('prnewswire.com', 'Press-release wire -- corporate announcements carried verbatim'),
    ('prnewswire.co.uk', 'Press-release wire -- corporate announcements carried verbatim'),
    ('globenewswire.com', 'Press-release wire -- corporate announcements carried verbatim'),
    ('news.google.com', 'Aggregator, not an outlet -- produced duplicate headlines and a bogus bias score'),
]

SEED_TITLE_KEYWORDS = [
    ('starred', 'Developer/repo noise, not news'),
    ('forked', 'Developer/repo noise, not news'),
    ('pull request', 'Developer/repo noise, not news'),
    ('merged', 'Developer/repo noise, not news'),
    ('repository', 'Developer/repo noise, not news'),
    ('npm package', 'Developer/repo noise, not news'),
    ('pypi', 'Developer/repo noise, not news'),
    ('added to pypi', 'Developer/repo noise, not news'),
    ('released on pypi', 'Developer/repo noise, not news'),
    ('week in review', 'Roundup/newsletter format, not an event'),
    ('patch tuesday', 'Developer/repo noise, not news'),
    ('added to npm', 'Developer/repo noise, not news'),
    ('new release:', 'Developer/repo noise, not news'),
    ('changelog:', 'Developer/repo noise, not news'),
    ('box office', 'Recap or transaction item, not an event'),
    ('box score', 'Recap or transaction item, not an event'),
    ('game recap', 'Recap or transaction item, not an event'),
    ('highlights:', 'Recap or transaction item, not an event'),
    ('traded to', 'Recap or transaction item, not an event'),
    ('signs with', 'Recap or transaction item, not an event'),
    ('scores in', 'Recap or transaction item, not an event'),
    ('Nintendo', 'Gaming coverage'),
    ('PlayStation', 'Gaming coverage'),
    ('Xbox', 'Gaming coverage'),
    ('Game review', 'Gaming coverage'),
    ('Gameplay', 'Gaming coverage'),
    ('eSports', 'Gaming coverage'),
    ('patch notes', 'Gaming coverage'),
    ('Twitch', 'Gaming coverage'),
    ('Fortnite', 'Gaming coverage'),
    ('Minecraft', 'Gaming coverage'),
    ('Pokemon', 'Gaming coverage'),
]


def upgrade():
    ingestion_blocks_table = op.create_table(
        "ingestion_blocks",
        sa.Column("id",        sa.Integer(),          nullable=False),
        sa.Column("kind",      sa.String(length=16),  nullable=False),
        sa.Column("pattern",   sa.String(),           nullable=False),
        sa.Column("note",      sa.String(),           nullable=True),
        sa.Column("is_active", sa.Boolean(),          nullable=False, server_default="true"),
        sa.Column("added_at",  sa.DateTime(),         nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "pattern", name="uq_ingestion_block_kind_pattern"),
    )

    now = datetime.utcnow()
    # Patterns are stored lowercase, matching what the admin routes write and
    # what get_ingestion_blocks() compares against. The source lists were mixed
    # case ("Nintendo", "PlayStation"); seeding them verbatim would let the
    # UI later add a lowercase duplicate past the unique constraint.
    rows = [
        {"kind": kind, "pattern": pattern.lower(), "note": note, "is_active": True, "added_at": now}
        for kind, entries in (
            ("source", SEED_SOURCES),
            ("title_keyword", SEED_TITLE_KEYWORDS),
        )
        for pattern, note in entries
    ]
    op.bulk_insert(ingestion_blocks_table, rows)


def downgrade():
    op.drop_table("ingestion_blocks")
