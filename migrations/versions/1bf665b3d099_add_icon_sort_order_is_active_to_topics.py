"""add icon, sort_order, is_active to topics

Revision ID: 1bf665b3d099
Revises: 1f2e3d4c5b6a
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa

revision = '1bf665b3d099'
down_revision = '1f2e3d4c5b6a'
branch_labels = None
depends_on = None

# Matches aggregator/constants.py:TOPICS at the time this migration was written.
SEED_TOPICS = [
    ("US Politics",         "UP", 0),
    ("US News",             "UN", 1),
    ("International News",  "IN", 2),
    ("Sci/Tech",            "ST", 3),
    ("Sports",              "SP", 4),
    ("Buss/Fin",            "BF", 5),
    ("Other",               "OT", 6),
]


def upgrade():
    op.add_column("topics", sa.Column("icon", sa.String(length=4), nullable=True))
    op.add_column("topics", sa.Column("sort_order", sa.Integer(), nullable=True))
    op.add_column("topics", sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"))

    topics = sa.table(
        "topics",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("icon", sa.String),
        sa.column("sort_order", sa.Integer),
    )

    conn = op.get_bind()
    for name, icon, sort_order in SEED_TOPICS:
        existing = conn.execute(sa.select(topics.c.id).where(topics.c.name == name)).first()
        if existing:
            conn.execute(
                topics.update()
                .where(topics.c.id == existing.id)
                .values(icon=icon, sort_order=sort_order)
            )
        else:
            conn.execute(topics.insert().values(name=name, icon=icon, sort_order=sort_order))

    # Any pre-existing Topic rows outside the seed list (e.g. orphaned legacy
    # topics per TODO.md) keep is_active=true from the column default and no
    # icon/sort_order — admin UI can fill those in or deactivate them.


def downgrade():
    op.drop_column("topics", "is_active")
    op.drop_column("topics", "sort_order")
    op.drop_column("topics", "icon")
