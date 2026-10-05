"""add user roles, active flag and login timestamps

Revision ID: c8e2f4a6b1d3
Revises: a4b7f0d92c31
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa


revision = 'c8e2f4a6b1d3'
down_revision = 'a4b7f0d92c31'
branch_labels = None
depends_on = None


# Three roles replace "logged in means full access": reader, scraper, admin
# (aggregator/models.py). Before this, every account could do everything, so
# every existing account becomes an admin. That keeps each one exactly as
# capable as it was; an admin can lower roles afterwards on /admin/users.
# New accounts default to reader.


def upgrade():
    op.add_column("users", sa.Column("role", sa.String(16), nullable=False, server_default="reader"))
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("created_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE users SET role = 'admin', is_admin = true")


def downgrade():
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "created_at")
    op.drop_column("users", "is_active")
    op.drop_column("users", "role")
