"""add pipeline_schedule table

Revision ID: b7d3f9a2c6e4
Revises: d4e9a1c7b5f3
Create Date: 2026-08-03
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7d3f9a2c6e4'
down_revision = 'd4e9a1c7b5f3'
branch_labels = None
depends_on = None

# Matches news_fetcher/scheduler.py's FETCH_SCHEDULE_HOURS ("7,12,17,22") and
# FULL_PIPELINE_HOURS ("7,17") env vars at the time this migration was
# written, before they were replaced by this table.
SEED_ENTRIES = [
    {"hour": 7,  "run_full_pipeline": True},
    {"hour": 12, "run_full_pipeline": False},
    {"hour": 17, "run_full_pipeline": True},
    {"hour": 22, "run_full_pipeline": False},
]


def upgrade():
    pipeline_schedule_table = op.create_table(
        "pipeline_schedule",
        sa.Column("id",                sa.Integer(), nullable=False),
        sa.Column("hour",              sa.Integer(), nullable=False),
        sa.Column("run_full_pipeline", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active",         sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hour", name="uq_pipeline_schedule_hour"),
    )

    rows = [{**entry, "is_active": True} for entry in SEED_ENTRIES]
    op.bulk_insert(pipeline_schedule_table, rows)


def downgrade():
    op.drop_table("pipeline_schedule")
