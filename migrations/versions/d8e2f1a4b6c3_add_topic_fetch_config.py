"""add topic fetch config columns

Adds admin-editable fetch configuration to the topics table so the scheduler
and the admin fetch page read their scheduled fetches from the DB instead of
hardcoded Python lists (FETCH_PRESETS / SCHEDULED_FETCHES).

New columns on topics:
  description     - human-readable note shown in the admin UI
  fetch_mode      - "query" | "top" | NULL (NULL = classification-only topic)
  fetch_country   - NewsAPI country filter, e.g. "de" (NULL = global)
  fetch_category  - NewsAPI category, e.g. "business"
  fetch_query     - NewsAPI search query for mode="query"
  gnews_query     - GNews search query (optional separate query)
  gnews_category  - GNews category, e.g. "nation", "world", "business"

Schema-only migration. Topic rows are seeded/updated idempotently by
seed_topics.py (run from bootstrap_admin.py and the admin reseed button).

Revision ID: d8e2f1a4b6c3
Revises: a3f7c2e9d1b8
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa


revision = 'd8e2f1a4b6c3'
down_revision = 'a3f7c2e9d1b8'
branch_labels = None
depends_on = None


def upgrade():
    # IF NOT EXISTS keeps this safe to re-run on DBs that partially have the
    # columns (mirrors the pattern in a3f7c2e9d1b8).
    op.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS description     TEXT")
    op.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS fetch_mode      VARCHAR(8)")
    op.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS fetch_country   VARCHAR(8)")
    op.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS fetch_category  VARCHAR(32)")
    op.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS fetch_query     TEXT")
    op.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS gnews_query     TEXT")
    op.execute("ALTER TABLE topics ADD COLUMN IF NOT EXISTS gnews_category  VARCHAR(32)")


def downgrade():
    op.execute("ALTER TABLE topics DROP COLUMN IF EXISTS gnews_category")
    op.execute("ALTER TABLE topics DROP COLUMN IF EXISTS gnews_query")
    op.execute("ALTER TABLE topics DROP COLUMN IF EXISTS fetch_query")
    op.execute("ALTER TABLE topics DROP COLUMN IF EXISTS fetch_category")
    op.execute("ALTER TABLE topics DROP COLUMN IF EXISTS fetch_country")
    op.execute("ALTER TABLE topics DROP COLUMN IF EXISTS fetch_mode")
    op.execute("ALTER TABLE topics DROP COLUMN IF EXISTS description")