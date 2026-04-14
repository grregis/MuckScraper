# muckscraperHeadlinesGoogleNEW/aggregator/models.py
# aggregator/models.py

from . import db
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

    articles = db.relationship("Article", backref="outlet", lazy=True)


class Topic(db.Model):
    __tablename__ = "topics"

    id   = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True, nullable=False)

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

    topics   = db.relationship("Topic",   secondary=story_topics,  back_populates="stories")
    articles = db.relationship("Article", backref="story", lazy=True)


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

    topics = db.relationship("Topic", secondary=article_topics, back_populates="articles")


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
