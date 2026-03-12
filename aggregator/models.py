# aggregator/models.py

from . import db
from datetime import datetime

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

    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String, nullable=False)
    summary    = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    topics   = db.relationship("Topic",   secondary=story_topics,  back_populates="stories")
    articles = db.relationship("Article", backref="story", lazy=True)


class Article(db.Model):
    __tablename__ = "articles"

    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String, nullable=False)
    content    = db.Column(db.Text)
    source     = db.Column(db.String)
    url        = db.Column(db.String, unique=True)
    outlet_id  = db.Column(db.Integer, db.ForeignKey("outlets.id"))
    story_id   = db.Column(db.Integer, db.ForeignKey("stories.id"))   # ADD THIS LINE
    date       = db.Column(db.DateTime, default=datetime.utcnow)
    bias_score = db.Column(db.Float)

    topics = db.relationship("Topic", secondary=article_topics, back_populates="articles")