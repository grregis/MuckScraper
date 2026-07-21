"""repair missing topics columns (icon, sort_order, is_active)

Some users who had the database stamped at 6153a50aa8fb before pulling
commit f674c85 ended up with the rss_feeds table present but without the
three columns that migration 1bf665b3d099 should have added to topics.
Alembic considers the DB up-to-date (already at head) so flask db upgrade
is a no-op for them.

This migration re-applies the same DDL and seed data using ALTER TABLE ...
ADD COLUMN IF NOT EXISTS, which is safe to run even when the columns already
exist (normal path), and fixes the DB for users in the broken state.

Revision ID: a3f7c2e9d1b8
Revises: 6153a50aa8fb
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa

revision = 'a3f7c2e9d1b8'
down_revision = '6153a50aa8fb'
branch_labels = None
depends_on = None

SEED_TOPICS = [
    ("US Politics",        "UP", 0),
    ("US News",            "UN", 1),
    ("International News", "IN", 2),
    ("Sci/Tech",           "ST", 3),
    ("Sports",             "SP", 4),
    ("Buss/Fin",           "BF", 5),
    ("Other",              "OT", 6),
]


def upgrade():
    # Use raw SQL so we can use IF NOT EXISTS — standard op.add_column()
    # raises if the column already exists.
    op.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS icon        VARCHAR(4)")
    op.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS sort_order  INTEGER")
    op.execute(
        "ALTER TABLE topics ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true"
    )

    topics = sa.table(
        "topics",
        sa.column("id",         sa.Integer),
        sa.column("name",       sa.String),
        sa.column("icon",       sa.String),
        sa.column("sort_order", sa.Integer),
    )

    conn = op.get_bind()
    for name, icon, sort_order in SEED_TOPICS:
        existing = conn.execute(
            sa.select(topics.c.id).where(topics.c.name == name)
        ).first()
        if existing:
            conn.execute(
                topics.update()
                .where(topics.c.id == existing.id)
                .values(icon=icon, sort_order=sort_order)
            )
        else:
            conn.execute(
                topics.insert().values(name=name, icon=icon, sort_order=sort_order)
            )


def downgrade():
    # Only drop columns that this migration may have added; leave rows alone.
    op.execute("ALTER TABLE topics DROP COLUMN IF EXISTS is_active")
    op.execute("ALTER TABLE topics DROP COLUMN IF EXISTS sort_order")
    op.execute("ALTER TABLE topics DROP COLUMN IF EXISTS icon")
