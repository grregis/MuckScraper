"""add headline_generated_at to stories

Lets the batch headline pass distinguish a stale headline (the story gained an
article after the headline was written) from a current one, now that headlines
are generated once per run instead of inline per article.

Existing headlines are backfilled as current, rather than left NULL. NULL reads
as "treat as stale", and on this database that would have made all 1,328
multi-article stories in the pass's 7-day window eligible on the first run when
only 45 actually lacked a headline -- roughly 27 minutes of needless LLM time,
and it would have rewritten the headlines of stories already in published
editions. The backfill stamps each existing headline as of its story's newest
article, so it reads as current and only genuinely changed stories regenerate.

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

    # Treat every headline that already exists as current as of its story's
    # newest article, so the first batch pass only picks up stories that are
    # actually missing a headline. Stories with no headline stay NULL and are
    # generated on the next run.
    op.execute(
        """
        UPDATE stories s
        SET headline_generated_at = p.newest_fetched_at
        FROM (
            SELECT story_id, MAX(fetched_at) AS newest_fetched_at
            FROM articles
            WHERE story_id IS NOT NULL
            GROUP BY story_id
        ) p
        WHERE p.story_id = s.id
          AND COALESCE(TRIM(s.headline), '') <> ''
          AND s.headline_generated_at IS NULL
        """
    )


def downgrade():
    with op.batch_alter_table('stories', schema=None) as batch_op:
        batch_op.drop_column('headline_generated_at')
