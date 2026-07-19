"""add rss_feeds table

Revision ID: 6153a50aa8fb
Revises: 1bf665b3d099
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

revision = '6153a50aa8fb'
down_revision = '1bf665b3d099'
branch_labels = None
depends_on = None

# Matches news_fetcher/rss_fetcher.py's RSS_FEEDS / RIGHT_ENRICHMENT_FEEDS /
# LEFT_ENRICHMENT_FEEDS at the time this migration was written. A URL can
# legitimately appear in more than one bucket.
GENERAL_FEEDS = [
    "https://feeds.apnews.com/rss/topnews",
    "https://feeds.reuters.com/reuters/topNews",
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://feeds.npr.org/1001/rss.xml",
    "https://www.pbs.org/newshour/feeds/rss/headlines",
    "https://www.economist.com/the-world-this-week/rss.xml",
    "https://rss.cnn.com/rss/edition.rss",
    "https://feeds.nbcnews.com/nbcnews/public/news",
    "https://feeds.washingtonpost.com/rss/world",
    "https://www.nytimes.com/svc/collections/v1/publish/https://www.nytimes.com/section/world/rss.xml",
    "https://www.theguardian.com/world/rss",
    "https://moxie.foxnews.com/google-publisher/latest.xml",
    "https://moxie.foxbusiness.com/google-publisher/latest.xml",
    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    "https://nypost.com/feed/",
    "https://www.washingtontimes.com/rss/headlines/news/politics/",
    "https://reason.com/feed/",
    "https://www.nationalreview.com/feed/",
    "https://thehill.com/feed/",
    "https://api.axios.com/feed/",
    "https://rss.politico.com/politics-news.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://nationalpost.com/feed/",
    "https://torontosun.com/feed/",
    "https://feeds.abcnews.com/abcnews/topstories",
    "https://www.cbsnews.com/latest/rss/main",
]

RIGHT_ENRICHMENT_FEEDS = [
    "https://moxie.foxnews.com/google-publisher/latest.xml",
    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    "https://nypost.com/feed/",
    "https://www.washingtonexaminer.com/rss",
    "https://www.washingtontimes.com/rss/headlines/news/politics/",
    "https://www.nationalreview.com/feed/",
    "https://moxie.foxbusiness.com/google-publisher/latest.xml",
    "https://www.newsmax.com/rss/Newsfront/16/",
    "https://www.dailywire.com/feeds/rss.xml",
]

LEFT_ENRICHMENT_FEEDS = [
    "https://rss.cnn.com/rss/edition.rss",
    "https://feeds.nbcnews.com/nbcnews/public/news",
    "https://feeds.washingtonpost.com/rss/world",
    "https://www.nytimes.com/svc/collections/v1/publish/https://www.nytimes.com/section/world/rss.xml",
    "https://www.theguardian.com/world/rss",
    "https://feeds.npr.org/1001/rss.xml",
    "https://www.cbsnews.com/latest/rss/main",
]


def upgrade():
    rss_feeds_table = op.create_table(
        "rss_feeds",
        sa.Column("id",       sa.Integer(),  nullable=False),
        sa.Column("url",      sa.String(),   nullable=False),
        sa.Column("bucket",   sa.String(length=32), nullable=False),
        sa.Column("label",    sa.String(),   nullable=True),
        sa.Column("enabled",  sa.Boolean(),  nullable=False, server_default="true"),
        sa.Column("added_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url", "bucket", name="uq_rss_feed_url_bucket"),
    )

    now = datetime.utcnow()
    rows = (
        [{"url": u, "bucket": "general", "enabled": True, "added_at": now} for u in GENERAL_FEEDS]
        + [{"url": u, "bucket": "right_enrichment", "enabled": True, "added_at": now} for u in RIGHT_ENRICHMENT_FEEDS]
        + [{"url": u, "bucket": "left_enrichment", "enabled": True, "added_at": now} for u in LEFT_ENRICHMENT_FEEDS]
    )
    op.bulk_insert(rss_feeds_table, rows)


def downgrade():
    op.drop_table("rss_feeds")
