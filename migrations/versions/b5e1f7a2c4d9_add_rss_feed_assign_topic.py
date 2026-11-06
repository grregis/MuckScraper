"""Add assign_topic to rss_feeds.

Revision ID: b5e1f7a2c4d9
Revises: a4c6e8b0d2f5
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa


revision = 'b5e1f7a2c4d9'
down_revision = 'a4c6e8b0d2f5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'rss_feeds',
        sa.Column('assign_topic', sa.Boolean(), server_default=sa.false(), nullable=False)
    )


def downgrade():
    op.drop_column('rss_feeds', 'assign_topic')
