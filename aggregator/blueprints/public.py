import os
import requests
import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from aggregator import db
from aggregator.models import Article, Story, Topic, RawArticlePayload
from aggregator.constants import TOPICS, AGGREGATORS

logger = logging.getLogger(__name__)

public = Blueprint("public", __name__)


def apply_aggregator_filter(story):
    from datetime import datetime as dt
    originals = []
    aggregators = []
    has_good_original = False
    seen_articles = set()
    sorted_articles = sorted(story.articles, key=lambda x: x.date or dt.min, reverse=True)
    for art in sorted_articles:
        key = (art.title, art.outlet_id)
        if key in seen_articles:
            continue
        seen_articles.add(key)
        outlet_name = art.outlet.name if art.outlet else ""
        if any(agg in outlet_name for agg in AGGREGATORS):
            aggregators.append(art)
        else:
            originals.append(art)
            if art.content and len(art.content) > 500:
                has_good_original = True
    story.display_articles = originals if has_good_original else (originals + aggregators)
    if not has_good_original:
        story.display_articles.sort(key=lambda x: x.date or dt.min, reverse=True)


@public.route("/")
def index():
    return redirect(url_for("admin.list_articles"))


@public.route("/story/<int:story_id>")
def view_story(story_id):
    from sqlalchemy.orm import joinedload
    from news_fetcher.summarizer import generate_deep_report, summarize_story, check_ollama_status
    story = Story.query.options(
        joinedload(Story.articles).joinedload(Article.outlet)
    ).get_or_404(story_id)

    ollama_online = check_ollama_status()

    if not story.summary or story.summary == story.title:
        if ollama_online:
            summary = summarize_story(story)
            if summary:
                story.summary = summary
                db.session.commit()

    if len(story.articles) >= 2 and not story.deep_report:
        if ollama_online:
            report = generate_deep_report(story)
            if report:
                story.deep_report = report
                db.session.commit()

    apply_aggregator_filter(story)

    return render_template("story.html", story=story, ollama_online=ollama_online)


@public.route("/article/<int:article_id>")
def view_article(article_id):
    from news_fetcher.summarizer import summarize_article, check_ollama_status
    article = Article.query.get_or_404(article_id)
    ollama_online = check_ollama_status()

    if not article.summary and ollama_online:
        summary = summarize_article(article)
        if summary:
            article.summary = summary
            db.session.commit()

    return render_template("article.html", article=article, ollama_online=ollama_online)


@public.route("/ollama-status")
def ollama_status():
    ollama_host = os.environ.get("OLLAMA_HOST", "")
    try:
        response = requests.get(f"{ollama_host}/api/tags", timeout=5)
        online = response.status_code == 200
    except Exception:
        online = False
    return jsonify({"online": online})
