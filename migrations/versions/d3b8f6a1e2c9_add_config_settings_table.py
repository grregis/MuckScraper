"""Add the config_settings table: LLM provider keys and models saved from
Settings, overriding the environment.

Revision ID: d3b8f6a1e2c9
Revises: a9c4e2f7b1d5
Create Date: 2026-10-22

API keys are stored only Fernet-encrypted (aggregator/secret_box.py), in
secret_value; value holds the non-secret settings (models, hosts, routing).
"""
from alembic import op
import sqlalchemy as sa

revision = 'd3b8f6a1e2c9'
down_revision = 'a9c4e2f7b1d5'
branch_labels = None
depends_on = None


def upgrade():
    # create_app() and the scheduler run db.create_all(), so on a running
    # install the table can already exist by the time this migration runs.
    if sa.inspect(op.get_bind()).has_table("config_settings"):
        return
    op.create_table(
        "config_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("secret_value", sa.Text(), nullable=True),
        sa.Column("hint", sa.String(length=8), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("(now() at time zone 'utc')")),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
    )


def downgrade():
    op.drop_table("config_settings")
