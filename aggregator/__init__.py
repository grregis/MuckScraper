# aggregator/__init__.py
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
import os

db = SQLAlchemy()


def create_app():
    app = Flask(__name__)

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:default1@postgres:5432/aggregator"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    from .models import Article, Story

    @app.route("/")
    def index():
        return redirect(url_for("list_articles"))

    @app.route("/articles")
    def list_articles():
        stories = Story.query.order_by(Story.created_at.desc()).limit(50).all()
        return render_template("articles.html", stories=stories)

    @app.route("/fetch", methods=["POST"])
    def fetch_articles():
        query = request.form.get("query", "").strip() or None
        from news_fetcher.fetch_and_store_articles import fetch_and_store_articles
        fetch_and_store_articles(query)
        return redirect(url_for("list_articles"))

    return app


def create_db(app):
    with app.app_context():
        db.create_all()