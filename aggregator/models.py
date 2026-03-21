# aggregator/models.py

from . import db
from datetime import datetime
from pgvector.sqlalchemy import Vector

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
    embedding  = db.Column(Vector(768))

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