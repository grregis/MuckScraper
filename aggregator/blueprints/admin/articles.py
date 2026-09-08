import logging
from datetime import datetime
from flask import render_template, request, redirect, url_for, jsonify
from flask_login import login_required
from sqlalchemy import case, func, or_
from aggregator import db
from aggregator.models import Article, Outlet, Story, Topic
from aggregator.search import SearchUnavailableError, search_story_ids
from aggregator.story_view import apply_aggregator_filter
from news_fetcher.llm_client import TIER_QUALITY

from . import admin, MANUAL_FETCH_STATUS_KEY, SCRAPE_STATUS_FILTERS
from ._shared import _load_json_setting, _save_json_setting, fetch_presets, story_bias_totals, redirect_to_articles, apply_scrape_result
from ._tasks import _start_bulk_task

logger = logging.getLogger(__name__)


@admin.route("/fetch-page")
@login_required
def fetch_page():
    return render_template(
        "fetch.html",
        fetch_presets=fetch_presets(),
        topics=Topic.query.filter_by(is_active=True).order_by(Topic.sort_order).all(),
        active_nav="fetch",
    )


@admin.route("/articles")
@login_required
def list_articles(per_page=25, force_multi=False):
    active_label = request.args.get("topic", None)
    active_scrape_status = request.args.get("scrape_status", "").strip().lower() or None
    active_search_query = request.args.get("q", "").strip() or None
    page = request.args.get("page", 1, type=int)
    show_single = request.args.get("show_single", "false") == "true"
    story_id = request.args.get("story_id", type=int)

    if active_scrape_status not in SCRAPE_STATUS_FILTERS:
        active_scrape_status = None

    if story_id:
        return redirect(url_for("public.view_story", story_id=story_id))

    if force_multi:
        show_single = False

    query = Story.query.join(Article).group_by(Story.id)
    meili_story_ids = None

    if not show_single:
        query = query.having(func.count(Article.id) > 1)

    if active_label:
        topic = Topic.query.filter_by(name=active_label).first()
        if topic:
            query = query.filter(Story.topics.contains(topic))
        else:
            query = query.filter(False)

    if active_scrape_status:
        query = query.filter(Story.articles.any(Article.scrape_status == active_scrape_status))

    if active_search_query:
        try:
            meili_story_ids = search_story_ids(active_search_query)
        except SearchUnavailableError as exc:
            logger.warning("Meilisearch unavailable, falling back to SQL search: %s", exc)

        if meili_story_ids is not None:
            if meili_story_ids:
                query = query.filter(Story.id.in_(meili_story_ids))
            else:
                query = query.filter(False)
        else:
            # Keep admin search fast by limiting it to shorter text fields.
            # The previous full-text search scanned large summary/content columns
            # and could time out on short terms like "ICE".
            search_terms = [term for term in active_search_query.split() if term]
            if not search_terms:
                search_terms = [active_search_query]
            for term in search_terms:
                like_term = f"%{term}%"
                query = query.filter(
                    or_(
                        Story.title.ilike(like_term),
                        Story.headline.ilike(like_term),
                        Story.topics.any(Topic.name.ilike(like_term)),
                        Story.articles.any(Article.title.ilike(like_term)),
                        Story.articles.any(Article.source.ilike(like_term)),
                        Story.articles.any(Article.outlet.has(Outlet.name.ilike(like_term))),
                    )
                )

    if meili_story_ids:
        order_by = [
            case({story_id: index for index, story_id in enumerate(meili_story_ids)}, value=Story.id),
            func.max(Article.date).desc(),
        ]
    else:
        order_by = [func.max(Article.date).desc()]

    pagination = query.order_by(*order_by).paginate(
        page=page, per_page=per_page, error_out=False
    )

    stories = pagination.items if pagination else []
    total_pages = pagination.pages if pagination else 0

    for story in stories:
        apply_aggregator_filter(story)
        story_bias_totals(story)

    if force_multi:
        active_nav = "grouped"
    elif active_label:
        active_nav = None
    else:
        active_nav = "all"

    return render_template(
        "articles.html",
        stories=stories,
        topics=Topic.query.filter_by(is_active=True).order_by(Topic.sort_order).all(),
        active_label=active_label,
        active_scrape_status=active_scrape_status,
        active_search_query=active_search_query,
        scrape_status_filters=SCRAPE_STATUS_FILTERS,
        page=page,
        total_pages=total_pages,
        show_single=show_single,
        is_multi_view=force_multi,
        active_nav=active_nav,
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
    scrape_status = request.form.get("scrape_status", "").strip() or None
    gnews_query = request.form.get("gnews_query", "").strip() or None
    gnews_category = request.form.get("gnews_category", "").strip() or None

    started_at = datetime.utcnow().isoformat()
    _save_json_setting(MANUAL_FETCH_STATUS_KEY, {
        "status": "running",
        "started_at": started_at,
        "label": label or "Custom",
    })
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
    finally:
        _save_json_setting(MANUAL_FETCH_STATUS_KEY, {
            "status": "idle",
            "started_at": started_at,
            "label": label or "Custom",
            "finished_at": datetime.utcnow().isoformat(),
        })

    return redirect_to_articles(label, scrape_status)


@admin.route("/enrich-story-balance/<int:story_id>", methods=["POST"])
@login_required
def enrich_story_balance(story_id):
    story = Story.query.get_or_404(story_id)
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    try:
        from news_fetcher.rss_fetcher import enrich_story_with_opposite_feeds
        from news_fetcher.fetch_and_store_articles import retry_unrated_outlets

        metrics = enrich_story_with_opposite_feeds(story, max_articles_per_story=3)
        if metrics.get("stored", 0) > 0:
            retry_unrated_outlets()
        logger.info(
            "[Admin] Story balance enrichment story_id=%s direction=%s stored=%s matched=%s status=%s",
            story_id,
            metrics.get("direction"),
            metrics.get("stored", 0),
            metrics.get("matched_articles", 0),
            metrics.get("status"),
        )
    except Exception as e:
        logger.exception(f"Story balance enrichment error for story {story_id}: {e}")
    return redirect_to_articles(label, scrape_status)


@admin.route("/summarize/<int:story_id>", methods=["POST"])
@login_required
def summarize_story_route(story_id):
    story = Story.query.get_or_404(story_id)
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    try:
        from news_fetcher.summarizer import summarize_story, check_ollama_status
        if check_ollama_status(TIER_QUALITY):
            summary = summarize_story(story)
            if summary:
                story.summary = summary
                db.session.commit()
    except Exception as e:
        logger.error(f"Summarization error: {e}")
    return redirect_to_articles(label, scrape_status)


@admin.route("/summarize-article/<int:article_id>", methods=["POST"])
@login_required
def summarize_article_route(article_id):
    article = Article.query.get_or_404(article_id)
    try:
        from news_fetcher.summarizer import summarize_article, check_ollama_status
        if check_ollama_status(TIER_QUALITY):
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
    scrape_status = request.form.get("scrape_status", "").strip() or None
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
    return redirect_to_articles(label, scrape_status)


@admin.route("/rate-article/<int:article_id>", methods=["POST"])
@login_required
def rate_article(article_id):
    article = Article.query.get_or_404(article_id)
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    try:
        from news_fetcher.outlet_bias_llm import get_article_bias_from_llm
        bias_score = get_article_bias_from_llm(article.title, article.content)
        if bias_score is not None:
            article.bias_score = bias_score
            db.session.commit()
    except Exception as e:
        logger.error(f"Article rating error: {e}")
    return redirect_to_articles(label, scrape_status)


@admin.route("/scrape-article/<int:article_id>", methods=["POST"])
@login_required
def scrape_article_route(article_id):
    article = Article.query.get_or_404(article_id)
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    try:
        from news_fetcher.scraper import scrape_article
        result = scrape_article(article.url, fallback_content=article.content, force=True)
        apply_scrape_result(article, result)
        db.session.commit()
    except Exception as e:
        logger.error(f"Scrape error: {e}")
    return redirect_to_articles(label, scrape_status)


@admin.route("/scrape-all-missing", methods=["POST"])
@login_required
def scrape_all_missing():
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    _start_bulk_task("scrape_all_missing", _scrape_all_missing_task)
    return redirect_to_articles(label, scrape_status)


def _scrape_all_missing_task():
    from news_fetcher.scraper import scrape_article, should_auto_rescrape_article
    candidates = Article.query.filter(
        (Article.content == None) |
        (Article.content == "") |
        (db.func.length(Article.content) < 500) |
        (Article.scrape_status.in_(["pending", "failed"]))
    ).order_by(Article.fetched_at.desc()).limit(100).all()
    eligible = [article for article in candidates if should_auto_rescrape_article(article)][:20]
    if eligible:
        for article in eligible:
            result = scrape_article(article.url, fallback_content=article.content)
            apply_scrape_result(article, result)
        db.session.commit()


@admin.route("/rescrape-article/<int:article_id>", methods=["POST"])
@login_required
def rescrape_article_route(article_id):
    article = Article.query.get_or_404(article_id)
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    try:
        from news_fetcher.scraper import scrape_article
        result = scrape_article(article.url, fallback_content=article.content, force=True)
        apply_scrape_result(article, result)
        db.session.commit()
    except Exception as e:
        logger.error(f"Rescrape error: {e}")
    return redirect_to_articles(label, scrape_status)


@admin.route("/deep-report/<int:story_id>", methods=["POST"])
@login_required
def deep_report_route(story_id):
    story = Story.query.get_or_404(story_id)
    label = request.form.get("label", "")
    try:
        if len(story.articles) >= 2:
            from news_fetcher.summarizer import generate_deep_report, check_ollama_status
            if check_ollama_status(TIER_QUALITY):
                report = generate_deep_report(story)
                if report:
                    story.deep_report = report
                    db.session.commit()
    except Exception as e:
        logger.error(f"Deep report error: {e}")
    if label:
        return redirect(url_for("admin.list_articles", topic=label))
    return redirect(url_for("admin.list_articles"))


@admin.route("/metrics")
@login_required
def metrics():
    return jsonify({
        "last_run_metrics": _load_json_setting("last_run_metrics"),
        "last_headline_site_metrics": _load_json_setting("last_headline_site_metrics"),
        "scrape_outcome_history": _load_json_setting("scrape_outcome_history_v1"),
    })
