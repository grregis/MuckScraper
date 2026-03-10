# aggregator/models.py

from . import db
from datetime import datetime


class Outlet(db.Model):
    __tablename__ = "outlets"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    url = db.Column(db.String)
    description = db.Column(db.Text)
    bias_score = db.Column(db.Float)

    articles = db.relationship("Article", backref="outlet", lazy=True)


class Topic(db.Model):
    __tablename__ = "topics"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, unique=True)

    stories = db.relationship("Story", backref="topic", lazy=True)


class Story(db.Model):
    __tablename__ = "stories"

    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String, nullable=False)
    summary = db.Column(db.Text)

    topic_id = db.Column(db.Integer, db.ForeignKey("topics.id"))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    articles = db.relationship("Article", backref="story", lazy=True)


class Article(db.Model):
    __tablename__ = "articles"

    id = db.Column(db.Integer, primary_key=True)

    title = db.Column(db.String, nullable=False)
    content = db.Column(db.Text)
    source = db.Column(db.String)
    url = db.Column(db.String, unique=True)

    outlet_id = db.Column(db.Integer, db.ForeignKey("outlets.id"))
    topic_id = db.Column(db.Integer, db.ForeignKey("topics.id"))
    story_id = db.Column(db.Integer, db.ForeignKey("stories.id"))

    date = db.Column(db.DateTime, default=datetime.utcnow)

    bias_score = db.Column(db.Float)