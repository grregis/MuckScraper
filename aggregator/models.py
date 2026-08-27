# muckscraperHeadlinesGoogleNEW/aggregator/models.py
# aggregator/models.py

from . import db
from .article_signals import is_roundup_article
from datetime import datetime
from pgvector.sqlalchemy import Vector
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# Many-to-many junction tables
story_topics = db.Table("story_topics",
    db.Column("story_id", db.Integer, db.ForeignKey("stories.id"), primary_key=True),
    db.Column("topic_id", db.Integer, db.ForeignKey("topics.id"), primary_key=True)
)

article_topics = db.Table("article_topics",
    db.Column("article_id", db.Integer, db.ForeignKey("articles.id"), primary_key=True),
    db.Column("topic_id", db.Integer, db.ForeignKey("topics.id"), primary_key=True)
)


class Outlet(db.Model):
    __tablename__ = "outlets"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String, nullable=False)
    url         = db.Column(db.String)
    description = db.Column(db.Text)
    bias_score  = db.Column(db.Float)
    bias_retry_count = db.Column(db.Integer, default=0, nullable=False, server_default='0')
    allsides_bias_score = db.Column(db.Float, nullable=True)
    bias_source = db.Column(db.String(16), nullable=True)

    articles = db.relationship("Article", backref="outlet", lazy=True)


class Topic(db.Model):
    __tablename__ = "topics"

    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String, unique=True, nullable=False)
    icon       = db.Column(db.String(4), nullable=True)
    sort_order = db.Column(db.Integer, nullable=True)
    is_active  = db.Column(db.Boolean, nullable=False, default=True, server_default="true")

    stories  = db.relationship("Story",   secondary=story_topics,  back_populates="topics")
    articles = db.relationship("Article", secondary=article_topics, back_populates="topics")


class Story(db.Model):
    __tablename__ = "stories"
    __table_args__ = (
        db.Index("ix_stories_created_at", "created_at"),
    )

    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String, nullable=False)
    headline   = db.Column(db.String)
    summary    = db.Column(db.Text)
    deep_report = db.Column(db.Text)
    headline_score = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    summary_generated_at = db.Column(db.DateTime, nullable=True)
    # When `headline` was last written, so the batch headline pass can tell a
    # stale headline (story gained an article since) from a current one. NULL
    # on every pre-existing row, which the pass treats as stale.
    headline_generated_at = db.Column(db.DateTime, nullable=True)

    topics   = db.relationship("Topic",   secondary=story_topics,  back_populates="stories")
    articles = db.relationship("Article", backref="story", lazy=True)
    edition_stories = db.relationship("EditionStory", back_populates="story", cascade="all, delete-orphan", overlaps="story")

    @property
    def last_updated(self):
        """Latest publication date among all articles in this story."""
        if not self.articles:
            return self.created_at
        return max((a.date for a in self.articles if a.date), default=self.created_at)

    @property
    def has_ai_headline(self):
        return len(self.articles) >= 2 and bool((self.headline or "").strip())

    @property
    def display_headline(self):
        return self.headline if self.has_ai_headline else self.title


class Article(db.Model):
    __tablename__ = "articles"
    __table_args__ = (
        db.Index("ix_articles_url",        "url"),
        db.Index("ix_articles_date",       "date"),
        db.Index("ix_articles_outlet_id",  "outlet_id"),
        db.Index("ix_articles_story_id",   "story_id"),
        db.Index("ix_articles_bias_score", "bias_score"),
    )

    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String, nullable=False)
    content    = db.Column(db.Text)
    source     = db.Column(db.String)
    url        = db.Column(db.String, unique=True)
    outlet_id  = db.Column(db.Integer, db.ForeignKey("outlets.id"))
    story_id   = db.Column(db.Integer, db.ForeignKey("stories.id"))
    date       = db.Column(db.DateTime, default=datetime.utcnow) # This is Published Date
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow) # When we actually scraped it
    bias_score = db.Column(db.Float)
    image_url  = db.Column(db.String)
    embedding  = db.Column(Vector(768))
    summary    = db.Column(db.Text)
    deep_analysis = db.Column(db.Text)
    scrape_audited = db.Column(db.Boolean, default=False, nullable=False, server_default='false')
    scrape_status = db.Column(db.String(32), nullable=False, default="pending", server_default='pending')
    scrape_method = db.Column(db.String(255), nullable=True)
    scrape_failure_reason = db.Column(db.String(1024), nullable=True)
    scrape_http_status = db.Column(db.Integer, nullable=True)
    grouping_match_method = db.Column(db.String(32), nullable=True)
    grouping_confidence = db.Column(db.Float, nullable=True)
    grouping_candidate_story_ids = db.Column(db.Text, nullable=True)
    grouping_needs_review = db.Column(db.Boolean, default=False, nullable=False, server_default='false')
    grouping_reviewed_at = db.Column(db.DateTime, nullable=True)

    topics = db.relationship("Topic", secondary=article_topics, back_populates="articles")

    @property
    def last_updated(self):
        """The publication date of this article."""
        return self.date

    @property
    def is_roundup(self):
        return is_roundup_article(self.title, self.url)

    @property
    def has_usable_image(self):
        return bool(self.image_url) and not self.is_roundup


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key   = db.Column(db.String, primary_key=True)
    value = db.Column(db.String)


class RawArticlePayload(db.Model):
    __tablename__ = "raw_article_payloads"
    __table_args__ = (
        db.Index("ix_raw_payload_fetched_at", "fetched_at"),
        db.Index("ix_raw_payload_source",     "source"),
    )

    id         = db.Column(db.Integer, primary_key=True)
    source     = db.Column(db.String, nullable=False)  # "newsapi" or "gnews"
    topic_name = db.Column(db.String, nullable=False)
    payload    = db.Column(db.Text, nullable=False)     # JSON string
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)

class EditorialHistory(db.Model):
    __tablename__ = 'editorial_history'

    id              = db.Column(db.Integer, primary_key=True)
    story_id        = db.Column(db.Integer, db.ForeignKey('stories.id', ondelete='CASCADE'), nullable=False)
    run_at          = db.Column(db.DateTime, nullable=False)
    editorial_rank  = db.Column(db.Integer, nullable=True)
    editorial_score = db.Column(db.Float, nullable=True)
    base_score      = db.Column(db.Float, nullable=True)
    final_score     = db.Column(db.Float, nullable=True)

    story = db.relationship('Story', backref=db.backref('editorial_history', lazy='dynamic', cascade='all, delete-orphan'))


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(64), unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    is_admin      = db.Column(db.Boolean, default=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class ScrapeBlocklist(db.Model):
    __tablename__ = "scrape_blocklist"
    __table_args__ = (
        db.Index("ix_scrape_blocklist_domain", "domain"),
    )

    id           = db.Column(db.Integer, primary_key=True)
    domain       = db.Column(db.String, unique=True, nullable=False)
    reason       = db.Column(db.String, nullable=False)
    added_at     = db.Column(db.DateTime, default=datetime.utcnow)
    is_permanent = db.Column(db.Boolean, default=False, nullable=False)


class IngestionBlock(db.Model):
    """
    Articles refused at ingestion, before anything is stored.

    Distinct from ScrapeBlocklist above, which is about domains whose *content*
    won't scrape -- those articles are still ingested and still count. These
    are never stored at all.

    `kind` picks what the pattern is tested against: "source" is a bare
    substring of the normalized URL, "title_keyword" a bare substring of the
    title. Both are matched case-insensitively. Substring rather than exact
    match is load-bearing for the league domains -- "nfl.com" is what catches
    the *-frontend.pocket.nfl.com hosts, and "nba.com" is what catches
    "wnba.com" -- so entries want to be specific enough not to over-match.
    """
    __tablename__ = "ingestion_blocks"
    __table_args__ = (
        db.UniqueConstraint("kind", "pattern", name="uq_ingestion_block_kind_pattern"),
    )

    id        = db.Column(db.Integer, primary_key=True)
    kind      = db.Column(db.String(16), nullable=False)  # source / title_keyword
    pattern   = db.Column(db.String, nullable=False)
    note      = db.Column(db.String, nullable=True)  # why this is blocked
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    added_at  = db.Column(db.DateTime, default=datetime.utcnow)


class RssFeed(db.Model):
    __tablename__ = "rss_feeds"
    __table_args__ = (
        # The same feed URL can legitimately appear in more than one bucket
        # (e.g. Fox News is both a general-ingestion feed and a right-enrichment
        # feed) — the three source lists in rss_fetcher.py are fetched
        # independently, so uniqueness is per (url, bucket), not per url alone.
        db.UniqueConstraint("url", "bucket", name="uq_rss_feed_url_bucket"),
    )

    id         = db.Column(db.Integer, primary_key=True)
    url        = db.Column(db.String, nullable=False)
    bucket     = db.Column(db.String(32), nullable=False)  # general / right_enrichment / left_enrichment
    label      = db.Column(db.String, nullable=True)
    enabled    = db.Column(db.Boolean, default=True, nullable=False)
    added_at   = db.Column(db.DateTime, default=datetime.utcnow)


class PromptTemplate(db.Model):
    __tablename__ = "prompt_templates"

    id           = db.Column(db.Integer, primary_key=True)
    key          = db.Column(db.String, unique=True, nullable=False)
    description  = db.Column(db.String, nullable=False)
    default_text = db.Column(db.Text, nullable=False)  # immutable original, for "reset to default"
    current_text = db.Column(db.Text, nullable=False)  # live/editable version actually used
    updated_at   = db.Column(db.DateTime, nullable=True)  # null = never customized


class PipelineSchedule(db.Model):
    """
    When the scheduler fetches/publishes. Each row is one cron-style firing
    time (hour, America/New_York) tagged as either a full pipeline run
    (fetch + grouping + summaries + edition publish) or a fetch-only run.
    """
    __tablename__ = "pipeline_schedule"
    __table_args__ = (
        db.UniqueConstraint("hour", name="uq_pipeline_schedule_hour"),
    )

    id                = db.Column(db.Integer, primary_key=True)
    hour              = db.Column(db.Integer, nullable=False)  # 0-23, America/New_York
    run_full_pipeline = db.Column(db.Boolean, default=False, nullable=False)
    is_active         = db.Column(db.Boolean, default=True, nullable=False)


class ScheduledFetch(db.Model):
    """
    *What* each scheduled run pulls from the news APIs -- the counterpart to
    PipelineSchedule, which is *when* runs happen.

    One row per category the pipeline queries. `mode` picks the NewsAPI
    endpoint: "query" hits /everything using `query`, "top" hits
    /top-headlines using `country` + `category`. The gnews_* columns are the
    same idea for the GNews provider, kept separate because the two providers
    have different category vocabularies and phrase-matching behaviour.
    """
    __tablename__ = "scheduled_fetches"
    __table_args__ = (
        # `label` keys this fetch's slice of the per-run metrics
        # (run_metrics["topics"]), so duplicate labels would silently
        # overwrite each other's results.
        db.UniqueConstraint("label", name="uq_scheduled_fetch_label"),
    )

    id               = db.Column(db.Integer, primary_key=True)
    label            = db.Column(db.String, nullable=False)
    description      = db.Column(db.String, nullable=True)  # blurb for the manual-fetch presets
    mode             = db.Column(db.String(16), nullable=False, default="query")  # query / top
    # newsapi_* rather than the bare names: a `query` attribute would shadow
    # Flask-SQLAlchemy's Model.query, and the prefix pairs with gnews_* below.
    newsapi_country  = db.Column(db.String(8), nullable=True)   # ISO-2, "top" mode only
    newsapi_category = db.Column(db.String(32), nullable=True)  # NewsAPI category, "top" mode only
    newsapi_query    = db.Column(db.String, nullable=True)      # search terms, "query" mode only
    gnews_query      = db.Column(db.String, nullable=True)
    gnews_category   = db.Column(db.String(32), nullable=True)
    sort_order       = db.Column(db.Integer, default=0, nullable=False)
    is_active        = db.Column(db.Boolean, default=True, nullable=False)


class Edition(db.Model):
    __tablename__ = 'editions'

    id           = db.Column(db.Integer, primary_key=True)
    date         = db.Column(db.Date, nullable=False)
    edition_type = db.Column(db.String(16), nullable=False)  # morning/evening
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    published    = db.Column(db.Boolean, default=True)

    __table_args__ = (db.UniqueConstraint('date', 'edition_type', name='uq_edition_date_type'),)

    edition_stories = db.relationship(
        'EditionStory',
        backref='edition',
        lazy='dynamic',
        order_by='EditionStory.rank',
        cascade='all, delete-orphan'
    )


class EditionStory(db.Model):
    __tablename__ = 'edition_stories'
    __table_args__ = (
        db.UniqueConstraint('edition_id', 'story_id',
                            name='uq_edition_story'),
    )

    id                      = db.Column(db.Integer, primary_key=True)
    edition_id              = db.Column(db.Integer, db.ForeignKey('editions.id'), nullable=False)
    story_id                = db.Column(db.Integer, db.ForeignKey('stories.id'), nullable=False)
    rank                    = db.Column(db.Integer, nullable=False)
    headline_score_at_publish = db.Column(db.Float, nullable=True)
    has_updates             = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    archived_image_path     = db.Column(db.String, nullable=True)
    source_image_url        = db.Column(db.String, nullable=True)
    image_credit_text       = db.Column(db.String(255), nullable=True)
    image_download_status   = db.Column(db.String(32), nullable=True)
    image_downloaded_at     = db.Column(db.DateTime, nullable=True)
    image_width             = db.Column(db.Integer, nullable=True)
    image_height            = db.Column(db.Integer, nullable=True)
    image_bytes             = db.Column(db.Integer, nullable=True)

    story = db.relationship('Story', back_populates='edition_stories', lazy='joined', overlaps="edition_stories,story_ref")
