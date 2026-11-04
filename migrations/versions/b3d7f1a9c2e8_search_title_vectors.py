"""Title vectors for ranking search results.

Revision ID: b3d7f1a9c2e8
Revises: e6f2a9c4b701
Create Date: 2026-11-03

Adds a small generated `title_vector` to stories (title and headline) and
articles (title), for databases that ran e6f2a9c4b701 before it added the
column too. Elsewhere the column already exists and this does nothing. Search ranks title matches first and checks them here
instead of in `search_vector`, which for an article holds up to 6,000
characters of text and is stored out of line, so reading it back for every
match was most of a broad search's time (aggregator/search.py). No index:
it is only read for rows the search_vector index already matched. The
expressions must match STORY_TITLE_VECTOR_SQL / ARTICLE_TITLE_VECTOR_SQL in
aggregator/models.py.

Where it runs, adding a stored generated column rewrites the table (about
35 seconds for 130,000 articles in testing).
"""
from alembic import op

revision = 'b3d7f1a9c2e8'
down_revision = 'e6f2a9c4b701'
branch_labels = None
depends_on = None

STORY_TITLE_VECTOR_SQL = (
    "setweight(to_tsvector('english'::regconfig, immutable_unaccent(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('english'::regconfig, immutable_unaccent(coalesce(headline, ''))), 'A')"
)
ARTICLE_TITLE_VECTOR_SQL = (
    "setweight(to_tsvector('english'::regconfig, immutable_unaccent(coalesce(title, ''))), 'A')"
)


def upgrade():
    op.execute(
        "ALTER TABLE stories ADD COLUMN IF NOT EXISTS title_vector tsvector "
        f"GENERATED ALWAYS AS ({STORY_TITLE_VECTOR_SQL}) STORED"
    )
    op.execute(
        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS title_vector tsvector "
        f"GENERATED ALWAYS AS ({ARTICLE_TITLE_VECTOR_SQL}) STORED"
    )


def downgrade():
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS title_vector")
    op.execute("ALTER TABLE stories DROP COLUMN IF EXISTS title_vector")
