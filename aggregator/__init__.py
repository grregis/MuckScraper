# aggregator/__init__.py
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
import os
import requests

db = SQLAlchemy()

TOPICS = [
    {"label": "US Headlines",    "mode": "top",   "query": None,                               "country": "us",  "category": None,         "gnews_query": None,           "gnews_category": "general"},
    {"label": "World Headlines", "mode": "top",   "query": None,                               "country": None,  "category": None,         "gnews_query": None,           "gnews_category": "world"},
    {"label": "US Politics",     "mode": "query", "query": "US politics congress white house", "country": None,  "category": None,         "gnews_query": "US politics",  "gnews_category": None},
    {"label": "Technology",      "mode": "top",   "query": None,                               "country": "us",  "category": "technology", "gnews_query": None,           "gnews_category": "technology"},
    {"label": "Gaming",          "mode": "top",   "query": None,                               "country": "us",  "category": "entertainment", "gnews_query": "gaming video games", "gnews_category": None},
]


def create_app():
    app = Flask(__name__)

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

    db.init_app(app)

    from .models import Article, Story, Topic

    @app.route("/")
    def index():
        return redirect(url_for("list_articles"))

    @app.route("/articles")
    def list_articles():
        active_label = request.args.get("topic", None)

        if active_label:
            # Filter stories that are tagged with this topic
            topic = Topic.query.filter_by(name=active_label).first()
            if topic:
                stories = (
                    Story.query
                    .filter(Story.topics.contains(topic))
                    .order_by(Story.created_at.desc())
                    .limit(50)
                    .all()
                )
            else:
                stories = []
        else:
            stories = Story.query.order_by(Story.created_at.desc()).limit(50).all()

        return render_template(
            "articles.html",
            stories=stories,
            topics=TOPICS,
            active_label=active_label
        )

    @app.route("/fetch", methods=["POST"])
    def fetch_articles():
        mode          = request.form.get("mode", "top").strip()
        query         = request.form.get("query", "").strip() or None
        country       = request.form.get("country", "").strip() or None
        category      = request.form.get("category", "").strip() or None
        label         = request.form.get("label", "").strip() or None
        gnews_query   = request.form.get("gnews_query", "").strip() or None
        gnews_category = request.form.get("gnews_category", "").strip() or None

        try:
            from news_fetcher.fetch_and_store_articles import fetch_and_store_articles
            fetch_and_store_articles(
                topic_name=label or "Custom",
                mode=mode,
                query=query,
                country=country,
                category=category,
                gnews_query=gnews_query,
                gnews_category=gnews_category,
            )
        except Exception as e:
            print(f"Fetch error: {e}")

        if label:
            return redirect(url_for("list_articles", topic=label))
        return redirect(url_for("list_articles"))

    @app.route("/summarize/<int:story_id>", methods=["POST"])
    def summarize_story_route(story_id):
        story = Story.query.get_or_404(story_id)
        label = request.form.get("label", "")

        try:
            from news_fetcher.summarizer import summarize_story, check_ollama_status
            if not check_ollama_status():
                print("Ollama is not reachable.")
            else:
                summary = summarize_story(story)
                if summary:
                    story.summary = summary
                    db.session.commit()
        except Exception as e:
            print(f"Summarization error: {e}")

        if label:
            return redirect(url_for("list_articles", topic=label))
        return redirect(url_for("list_articles"))

    @app.route("/rerank-outlet/<int:outlet_id>", methods=["POST"])
    def rerank_outlet(outlet_id):
        from .models import Outlet
        outlet = Outlet.query.get_or_404(outlet_id)
        label  = request.form.get("label", "")

        try:
            from news_fetcher.outlet_bias_llm import get_outlet_bias_from_llm
            bias_score = get_outlet_bias_from_llm(outlet.name)
            if bias_score is not None:
                outlet.bias_score = bias_score
                for article in outlet.articles:
                    article.bias_score = bias_score
                db.session.commit()
        except Exception as e:
            print(f"Re-rank error: {e}")

        if label:
            return redirect(url_for("list_articles", topic=label))
        return redirect(url_for("list_articles"))

    @app.route("/rate-article/<int:article_id>", methods=["POST"])
    def rate_article(article_id):
        article = Article.query.get_or_404(article_id)
        label   = request.form.get("label", "")

        try:
            from news_fetcher.outlet_bias_llm import get_article_bias_from_llm
            bias_score = get_article_bias_from_llm(article.title, article.content)
            if bias_score is not None:
                article.bias_score = bias_score
                db.session.commit()
        except Exception as e:
            print(f"Article rating error: {e}")

        if label:
            return redirect(url_for("list_articles", topic=label))
        return redirect(url_for("list_articles"))

    @app.route("/ollama-status")
    def ollama_status():
        ollama_host = os.environ.get("OLLAMA_HOST", "")
        try:
            response = requests.get(f"{ollama_host}/api/tags", timeout=5)
            online = response.status_code == 200
        except Exception:
            online = False
        return jsonify({"online": online})

    return app


def create_db(app):
    with app.app_context():
        db.create_all()