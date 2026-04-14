import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_login import login_required
from aggregator import db
from aggregator.models import Article, Story, Topic, RawArticlePayload
from aggregator.constants import TOPICS, AGGREGATORS

logger = logging.getLogger(__name__)

admin = Blueprint("admin", __name__)


def apply_aggregator_filter(story):
    from datetime import datetime as dt
    originals = []
    aggregators_list = []
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
            aggregators_list.append(art)
        else:
            originals.append(art)
            if art.content and len(art.content) > 500:
                has_good_original = True
    story.display_articles = originals if has_good_original else (originals + aggregators_list)
    if not has_good_original:
        story.display_articles.sort(key=lambda x: x.date or dt.min, reverse=True)


@admin.route("/articles")
@login_required
def list_articles(per_page=25, force_multi=False):
    from sqlalchemy import func
    active_label = request.args.get("topic", None)
    page = request.args.get("page", 1, type=int)
    show_single = request.args.get("show_single", "false") == "true"
    story_id = request.args.get("story_id", type=int)

    if story_id:
        return redirect(url_for("public.view_story", story_id=story_id))

    if force_multi:
        show_single = False

    query = Story.query.join(Article).group_by(Story.id)

    if not show_single:
        query = query.having(func.count(Article.id) > 1)

    if active_label:
        topic = Topic.query.filter_by(name=active_label).first()
        if topic:
            query = query.filter(Story.topics.contains(topic))
        else:
            query = query.filter(False)

    pagination = query.order_by(func.max(Article.date).desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    stories = pagination.items if pagination else []
    total_pages = pagination.pages if pagination else 0

    for story in stories:
        apply_aggregator_filter(story)

    return render_template(
        "articles.html",
        stories=stories,
        topics=TOPICS,
        active_label=active_label,
        page=page,
        total_pages=total_pages,
        show_single=show_single,
        is_multi_view=force_multi
    )


@admin.route("/multi-stories")
@login_required
def multi_article_stories():
    return list_articles(per_page=50, force_multi=True)


@admin.route("/fetch", methods=["POST"])
@login_required
def fetch_articles():
    mode = request.form.get("mode", "top").strip()
    query = request.form.get("query", "").strip() or None
    country = request.form.get("country", "").strip() or None
    category = request.form.get("category", "").strip() or None
    label = request.form.get("label", "").strip() or None
    gnews_query = request.form.get("gnews_query", "").strip() or None
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
        logger.error(f"Fetch error: {e}")

    if label:
        return redirect(url_for("admin.list_articles", topic=label))
    return redirect(url_for("admin.list_articles"))


@admin.route("/summarize/<int:story_id>", methods=["POST"])
@login_required
def summarize_story_route(story_id):
    story = Story.query.get_or_404(story_id)
    label = request.form.get("label", "")
    try:
        from news_fetcher.summarizer import summarize_story, check_ollama_status
        if check_ollama_status():
            summary = summarize_story(story)
            if summary:
                story.summary = summary
                db.session.commit()
    except Exception as e:
        logger.error(f"Summarization error: {e}")
    if label:
        return redirect(url_for("admin.list_articles", topic=label))
    return redirect(url_for("admin.list_articles"))


@admin.route("/summarize-article/<int:article_id>", methods=["POST"])
@login_required
def summarize_article_route(article_id):
    article = Article.query.get_or_404(article_id)
    try:
        from news_fetcher.summarizer import summarize_article, check_ollama_status
        if check_ollama_status():
            summary = summarize_article(article)
            if summary:
                article.summary = summary
                db.session.commit()
    except Exception as e:
        logger.error(f"Article summarization error: {e}")
    return redirect(url_for("public.view_article", article_id=article_id))


@admin.route("/rerank-outlet/<int:outlet_id>", methods=["POST"])
@login_required
def rerank_outlet(outlet_id):
    from aggregator.models import Outlet
    outlet = Outlet.query.get_or_404(outlet_id)
    label = request.form.get("label", "")
    try:
        from news_fetcher.outlet_bias_llm import get_outlet_bias_from_llm
        bias_score = get_outlet_bias_from_llm(outlet.name)
        if bias_score is not None:
            outlet.bias_score = bias_score
            for article in outlet.articles:
                article.bias_score = bias_score
            db.session.commit()
    except Exception as e:
        logger.error(f"Re-rank error: {e}")
    if label:
        return redirect(url_for("admin.list_articles", topic=label))
    return redirect(url_for("admin.list_articles"))


@admin.route("/rate-article/<int:article_id>", methods=["POST"])
@login_required
def rate_article(article_id):
    article = Article.query.get_or_404(article_id)
    label = request.form.get("label", "")
    try:
        from news_fetcher.outlet_bias_llm import get_article_bias_from_llm
        bias_score = get_article_bias_from_llm(article.title, article.content)
        if bias_score is not None:
            article.bias_score = bias_score
            db.session.commit()
    except Exception as e:
        logger.error(f"Article rating error: {e}")
    if label:
        return redirect(url_for("admin.list_articles", topic=label))
    return redirect(url_for("admin.list_articles"))


@admin.route("/ollama-catchup", methods=["POST"])
@login_required
def ollama_catchup_route():
    label = request.form.get("label", "")
    try:
        from news_fetcher.fetch_and_store_articles import ollama_catchup
        ollama_catchup()
    except Exception as e:
        logger.error(f"Catchup error: {e}")
    return redirect(url_for("admin.list_articles", topic=label) if label else url_for("admin.list_articles"))


@admin.route("/scrape-article/<int:article_id>", methods=["POST"])
@login_required
def scrape_article_route(article_id):
    article = Article.query.get_or_404(article_id)
    label = request.form.get("label", "")
    try:
        from news_fetcher.scraper import scrape_article
        content = scrape_article(article.url)
        if content:
            article.content = content
            db.session.commit()
    except Exception as e:
        logger.error(f"Scrape error: {e}")
    if label:
        return redirect(url_for("admin.list_articles", topic=label))
    return redirect(url_for("admin.list_articles"))


@admin.route("/scrape-all-missing", methods=["POST"])
@login_required
def scrape_all_missing():
    label = request.form.get("label", "")
    try:
        from news_fetcher.scraper import scrape_article
        missing = Article.query.filter(
            (Article.content == None) |
            (Article.content == "") |
            (db.func.length(Article.content) < 500)
        ).limit(20).all()
        if missing:
            for article in missing:
                content = scrape_article(article.url)
                if content:
                    article.content = content
            db.session.commit()
    except Exception as e:
        logger.error(f"Scrape all error: {e}")
    if label:
        return redirect(url_for("admin.list_articles", topic=label))
    return redirect(url_for("admin.list_articles"))


@admin.route("/rescrape-article/<int:article_id>", methods=["POST"])
@login_required
def rescrape_article_route(article_id):
    article = Article.query.get_or_404(article_id)
    label = request.form.get("label", "")
    try:
        from news_fetcher.scraper import scrape_article
        content = scrape_article(article.url)
        if content:
            article.content = content
            db.session.commit()
    except Exception as e:
        logger.error(f"Rescrape error: {e}")
    if label:
        return redirect(url_for("admin.list_articles", topic=label))
    return redirect(url_for("admin.list_articles"))
@admin.route("/force-regroup", methods=["POST"])
@login_required
def force_regroup():
    label = request.form.get("label", "")
    try:
        from news_fetcher.fetch_and_store_articles import force_regroup_all
        force_regroup_all()
    except Exception as e:
        logger.exception(f"Force regroup error: {e}")
    if label:
        return redirect(url_for("admin.list_articles", topic=label))
    return redirect(url_for("admin.list_articles"))


@admin.route("/force-resummarize", methods=["POST"])
@login_required
def force_resummarize():
    label = request.form.get("label", "")
    try:
        from news_fetcher.fetch_and_store_articles import force_resummarize_all
        force_resummarize_all()
    except Exception as e:
        logger.exception(f"Force resummarize error: {e}")
    if label:
        return redirect(url_for("admin.list_articles", topic=label))
    return redirect(url_for("admin.list_articles"))


@admin.route("/reclassify-articles", methods=["POST"])

@login_required
def wake_ollama():
    import os
    import wakeonlan
    label = request.form.get("label", "")
    try:
        mac = os.environ.get("OLLAMA_MAC", "")
        if mac:
            wakeonlan.send_magic_packet(mac)
    except Exception as e:
        logger.error(f"WoL error: {e}")
    return redirect(url_for("admin.list_articles", topic=label) if label else url_for("admin.list_articles"))


@admin.route("/reclassify-articles", methods=["POST"])
@login_required
def reclassify_articles():
    label = request.form.get("label", "")
    try:
        from news_fetcher.fetch_and_store_articles import reclassify_all_articles
        reclassify_all_articles()
    except Exception as e:
        logger.exception(f"Reclassify error: {e}")
    return redirect(url_for("admin.list_articles", topic=label) if label else url_for("admin.list_articles"))


@admin.route("/deep-report/<int:story_id>", methods=["POST"])
@login_required
def deep_report_route(story_id):
    story = Story.query.get_or_404(story_id)
    label = request.form.get("label", "")
    try:
        if len(story.articles) >= 2:
            from news_fetcher.summarizer import generate_deep_report, check_ollama_status
            if check_ollama_status():
                report = generate_deep_report(story)
                if report:
                    story.deep_report = report
                    db.session.commit()
    except Exception as e:
        logger.error(f"Deep report error: {e}")
    if label:
        return redirect(url_for("admin.list_articles", topic=label))
    return redirect(url_for("admin.list_articles"))


@admin.route("/scrape-blocklist")
@login_required
def scrape_blocklist():
    from aggregator.models import ScrapeBlocklist
    entries = ScrapeBlocklist.query.order_by(
        ScrapeBlocklist.is_permanent.desc(),
        ScrapeBlocklist.added_at.desc()
    ).all()
    return render_template("scrape_blocklist.html", entries=entries)


@admin.route("/audit-scrapes", methods=["POST"])
@login_required
def audit_scrapes():
    label = request.form.get("label", "")
    try:
        from news_fetcher.fetch_and_store_articles import audit_existing_scrapes
        audit_existing_scrapes()
    except Exception as e:
        logger.exception(f"Audit error: {e}")
    return redirect(url_for("admin.scrape_blocklist"))


@admin.route("/unblock-domain", methods=["POST"])
@login_required
def unblock_domain():
    from aggregator.models import ScrapeBlocklist
    domain = request.form.get("domain", "").strip()
    if domain:
        entry = ScrapeBlocklist.query.filter_by(domain=domain, is_permanent=False).first()
        if entry:
            db.session.delete(entry)
            db.session.commit()
            logger.info(f"[Blocklist] Removed {domain}")
    return redirect(url_for("admin.scrape_blocklist"))
