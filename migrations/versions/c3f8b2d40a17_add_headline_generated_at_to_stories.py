"""add headline_generated_at to stories

Lets the batch headline pass distinguish a stale headline (the story gained an
article after the headline was written) from a current one, now that headlines
are generated once per run instead of inline per article.

Deliberately left NULL for existing rows rather than backfilled: NULL reads as
"unknown, treat as stale", so every multi-article story gets one fresh headline
on the first run after deploy and is then current.

Revision ID: c3f8b2d40a17
Revises: b7d3f9a2c6e4
Create Date: 2026-08-11 01:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3f8b2d40a17'
down_revision = 'b7d3f9a2c6e4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('stories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('headline_generated_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('stories', schema=None) as batch_op:
        batch_op.drop_column('headline_generated_at')
