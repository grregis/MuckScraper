"""add editorial history table

Revision ID: a1b2c3d4e5f6
Revises: 37ac048a5fea
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = '894b9d0caad3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'editorial_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('story_id', sa.Integer(), sa.ForeignKey('stories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('run_at', sa.DateTime(), nullable=False),
        sa.Column('editorial_rank', sa.Integer(), nullable=True),
        sa.Column('editorial_score', sa.Float(), nullable=True),
        sa.Column('base_score', sa.Float(), nullable=True),
        sa.Column('final_score', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_editorial_history_story_id', 'editorial_history', ['story_id'])
    op.create_index('ix_editorial_history_run_at', 'editorial_history', ['run_at'])


def downgrade():
    op.drop_table('editorial_history')