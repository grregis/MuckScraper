import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from aggregator.search import healthcheck as meili_healthcheck
from aggregator.models import Article, Story, Topic, RawArticlePayload
from aggregator.constants import TOPICS
from aggregator.story_view import apply_aggregator_filter
from news_fetcher.llm_client import check_llm_status as check_ollama_status

logger = logging.getLogger(__name__)

public = Blueprint("public", __name__)


@public.route("/")
def index():
    return redirect(url_for("admin.list_articles"))


@public.route("/feed-headlines")
def aggregator_headlines():
    from aggregator.models import Story, Article
    from datetime import datetime, timedelta
    from aggregator.constants import TOPICS
    cutoff = datetime.utcnow() - timedelta(days=1)
    stories = Story.query.join(Article).group_by(Story.id).filter(
        Story.created_at >= cutoff,
        Story.headline_score > 0
    ).order_by(Story.headline_score.desc()).limit(20).all()
    
    for story in stories:
        apply_aggregator_filter(story)
        
    return render_template(
        'articles.html',
        stories=stories,
        topics=TOPICS,
        active_label=None,
        page=1,
        total_pages=1,
        show_single=True,
        is_multi_view=False
    )


@public.route("/story/<int:story_id>")
def view_story(story_id):
    from sqlalchemy.orm import joinedload
    story = Story.query.options(
        joinedload(Story.articles).joinedload(Article.outlet)
    ).get_or_404(story_id)

    ollama_online = check_ollama_status()

    apply_aggregator_filter(story)

    return render_template("story.html", story=story, ollama_online=ollama_online)


@public.route("/article/<int:article_id>")
def view_article(article_id):
    article = Article.query.get_or_404(article_id)
    ollama_online = check_ollama_status()

    return render_template("article.html", article=article, ollama_online=ollama_online)


@public.route("/ollama-status")
def ollama_status():
    return jsonify({"online": check_ollama_status()})


@public.route("/meili-status")
def meili_status():
    return jsonify({"online": meili_healthcheck()})
