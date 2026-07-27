import logging
import json
import threading
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from flask import Blueprint, current_app, render_template, request, redirect, url_for, jsonify
from flask_login import login_required
from sqlalchemy import case, func, or_
from aggregator import db
from aggregator.models import AppSetting, Article, Outlet, Story, Topic, RawArticlePayload, RssFeed
from aggregator.search import SearchUnavailableError, reindex_all, search_story_ids
from aggregator.story_view import apply_aggregator_filter

logger = logging.getLogger(__name__)

admin = Blueprint("admin", __name__)
SEARCH_REINDEX_STATUS_KEY = "search_reindex_status_v1"
SCRAPE_STATUS_FILTERS = ("success", "fallback", "blocked", "failed", "skipped", "pending")
FETCH_PRESETS = [
    {
        "label": "US Politics",
        "description": "Congress, White House, courts, elections",
        "mode": "query",
        "country": "",
        "category": "",
        "query": "US politics congress white house senate supreme court",
        "gnews_query": "US politics congress white house",
        "gnews_category": "",
    },
    {
        "label": "Business & Economy",
        "description": "Top business headlines",
        "mode": "top",
        "country": "us",
        "category": "business",
        "query": "",
        "gnews_query": "",
        "gnews_category": "business",
    },
    {
        "label": "Science & Health",
        "description": "Research, medicine, technology",
        "mode": "query",
        "country": "",
        "category": "",
        "query": "scientific breakthroughs medical research healthcare tech",
        "gnews_query": "science health research",
        "gnews_category": "science",
    },
    {
        "label": "Sports",
        "description": "Top sports headlines",
        "mode": "top",
        "country": "us",
        "category": "sports",
        "query": "",
        "gnews_query": "",
        "gnews_category": "sports",
    },
    {
        "label": "World News",
        "description": "International news, conflict, diplomacy",
        "mode": "query",
        "country": "",
        "category": "",
        "query": "international world global news conflicts diplomacy",
        "gnews_query": "world global news",
        "gnews_category": "world",
    },
]


def _load_json_setting(key):
    setting = AppSetting.query.filter_by(key=key).first()
    if not setting or not setting.value:
        return None
    try:
        return json.loads(setting.value)
    except Exception:
        logger.warning("Failed to parse AppSetting JSON for key=%s", key)
        return {
            "status": "parse_error",
            "raw_value": setting.value,
        }


def _save_json_setting(key, payload):
    setting = AppSetting.query.filter_by(key=key).first()
    if setting:
        setting.value = json.dumps(payload)
    else:
        db.session.add(AppSetting(key=key, value=json.dumps(payload)))
    db.session.commit()


def _try_claim_task_status(key, running_payload):
    """
    Atomically claim a task-status row for 'running', safe across multiple
    gunicorn worker processes (not just threads within one process).

    A plain "check status, then save 'running'" -- even guarded by an
    in-process threading.Lock -- only prevents a double-start from threads in
    the SAME process. With multiple worker processes, two requests landing on
    different workers would each pass the check independently (each process
    has its own lock and Python globals) and both start the same action.

    This does the check-and-set as one atomic SQL statement instead: the
    UPDATE (or INSERT, if the row doesn't exist yet) only takes effect if no
    other process already has the row marked 'running', and the database's
    row-level locking makes that atomic regardless of how many processes are
    asking at once. Returns True if this call won the claim, False if the
    action is already running (started by any process/thread).
    """
    payload_json = json.dumps(running_payload)
    result = db.session.execute(
        db.text("""
            INSERT INTO app_settings (key, value)
            VALUES (:key, :value)
            ON CONFLICT (key) DO UPDATE
                SET value = :value
                WHERE (app_settings.value::json ->> 'status') IS DISTINCT FROM 'running'
            RETURNING key
        """),
        {"key": key, "value": payload_json},
    )
    claimed = result.fetchone() is not None
    db.session.commit()
    return claimed


def _search_reindex_status_payload():
    payload = _load_json_setting(SEARCH_REINDEX_STATUS_KEY)
    if payload:
        return payload
    return {
        "status": "idle",
        "started_at": None,
        "finished_at": None,
        "message": "Search index has not been rebuilt in this app session yet.",
        "story_documents": None,
        "article_documents": None,
    }


def _ai_task_status_key(task_type, resource_id):
    return f"ai_task_status_v1:{task_type}:{resource_id}"


def _ai_task_default_message(task_type):
    messages = {
        "story_summary": "Story summary has not been started in this app session yet.",
        "story_deep_report": "Story analysis has not been started in this app session yet.",
        "article_summary": "Article summary has not been started in this app session yet.",
    }
    return messages.get(task_type, "AI task has not been started in this app session yet.")


def _ai_task_status_payload(task_type, resource_id):
    payload = _load_json_setting(_ai_task_status_key(task_type, resource_id))
    if payload:
        return payload
    return {
        "status": "idle",
        "task_type": task_type,
        "resource_id": resource_id,
        "started_at": None,
        "finished_at": None,
        "message": _ai_task_default_message(task_type),
    }


def _save_ai_task_status(task_type, resource_id, payload):
    base_payload = {
        "task_type": task_type,
        "resource_id": resource_id,
    }
    base_payload.update(payload)
    _save_json_setting(_ai_task_status_key(task_type, resource_id), base_payload)


def _run_ai_task(app, task_type, resource_id):
    with app.app_context():
        started_at = _ai_task_status_payload(task_type, resource_id).get("started_at")

        try:
            from news_fetcher.summarizer import (
                summarize_story,
                summarize_article,
                generate_deep_report,
                check_ollama_status,
            )

            if not check_ollama_status():
                raise RuntimeError("Ollama is offline.")

            if task_type == "story_summary":
                story = Story.query.get_or_404(resource_id)
                summary = summarize_story(story)
                if not summary:
                    raise RuntimeError("No story summary was generated.")
                story.summary = summary
                db.session.commit()
                message = "Story summary completed successfully."
            elif task_type == "story_deep_report":
                story = Story.query.get_or_404(resource_id)
                if len(story.articles) < 2:
                    raise RuntimeError("Story analysis requires at least two articles.")
                report = generate_deep_report(story)
                if not report:
                    raise RuntimeError("No story analysis was generated.")
                story.deep_report = report
                db.session.commit()
                message = "Story analysis completed successfully."
            elif task_type == "article_summary":
                article = Article.query.get_or_404(resource_id)
                summary = summarize_article(article)
                if not summary:
                    raise RuntimeError("No article summary was generated.")
                article.summary = summary
                db.session.commit()
                message = "Article summary completed successfully."
            else:
                raise RuntimeError(f"Unknown AI task type: {task_type}")

            _save_ai_task_status(
                task_type,
                resource_id,
                {
                    "status": "success",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": message,
                },
            )
        except Exception as e:
            db.session.rollback()
            logger.exception("Async AI task error type=%s resource_id=%s: %s", task_type, resource_id, e)
            _save_ai_task_status(
                task_type,
                resource_id,
                {
                    "status": "error",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": str(e),
                },
            )


def _run_search_reindex(app):
    with app.app_context():
        try:
            started_at = _search_reindex_status_payload().get("started_at")
            counts = reindex_all()
            _save_json_setting(
                SEARCH_REINDEX_STATUS_KEY,
                {
                    "status": "success",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": "Meilisearch reindex completed successfully.",
                    "story_documents": counts["story_documents"],
                    "article_documents": counts["article_documents"],
                },
            )
            logger.info(
                "[Search] Async reindex complete stories=%s articles=%s",
                counts["story_documents"],
                counts["article_documents"],
            )
        except Exception as e:
            logger.exception(f"Async search reindex error: {e}")
            started_at = _search_reindex_status_payload().get("started_at")
            _save_json_setting(
                SEARCH_REINDEX_STATUS_KEY,
                {
                    "status": "error",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": str(e),
                    "story_documents": None,
                    "article_documents": None,
                },
            )


BULK_TASK_ACTIONS = {
    "ollama_catchup",
    "scrape_all_missing",
    "force_regroup",
    "force_resummarize",
    "reclassify_articles",
    "audit_scrapes",
}


def _bulk_task_status_key(action):
    return f"bulk_task_status_v1:{action}"


def _bulk_task_status_payload(action):
    payload = _load_json_setting(_bulk_task_status_key(action))
    if payload:
        return payload
    return {
        "status": "idle",
        "started_at": None,
        "finished_at": None,
        "message": f"{action} has not been run in this app session yet.",
    }


def _run_bulk_task(app, action, fn):
    with app.app_context():
        started_at = _bulk_task_status_payload(action).get("started_at")
        try:
            fn()
            _save_json_setting(
                _bulk_task_status_key(action),
                {
                    "status": "success",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": f"{action} completed successfully.",
                },
            )
            logger.info(f"[BulkTask] {action} completed")
        except Exception as e:
            db.session.rollback()
            logger.exception(f"[BulkTask] {action} failed: {e}")
            _save_json_setting(
                _bulk_task_status_key(action),
                {
                    "status": "error",
                    "started_at": started_at,
                    "finished_at": datetime.utcnow().isoformat(),
                    "message": str(e),
                },
            )


def _start_bulk_task(action, fn):
    """
    Run a long-running admin action (reclassify, resummarize, regroup, scrape
    backlog, etc.) in a background thread instead of inline in the request.
    These can take minutes on a full DB, which was blowing past gunicorn's
    worker timeout and killing the request mid-run (GitHub issue #3).

    Returns (started, status) — started is False (with the current running
    status) if this action is already in progress on any worker process.
    """
    started_at = datetime.utcnow().isoformat()
    claimed = _try_claim_task_status(
        _bulk_task_status_key(action),
        {
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "message": f"{action} is running.",
        },
    )
    if not claimed:
        return False, _bulk_task_status_payload(action)

    app = current_app._get_current_object()
    thread = threading.Thread(target=_run_bulk_task, args=(app, action, fn), daemon=True)
    thread.start()
    return True, _bulk_task_status_payload(action)


def article_domain(url):
    if not url:
        return None
    domain = urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain or None


def story_bias_totals(story):
    counts = {
        "left": 0,
        "center": 0,
        "right": 0,
    }
    for article in story.articles:
        score = article.bias_score
        if score is None and article.outlet:
            score = article.outlet.bias_score
        if score is None:
            continue
        if score <= 2.5:
            counts["left"] += 1
        elif score <= 3.5:
            counts["center"] += 1
        else:
            counts["right"] += 1

    story.left_bias_count = counts["left"]
    story.center_bias_count = counts["center"]
    story.right_bias_count = counts["right"]
    story.bias_gap = abs(counts["left"] - counts["right"])
    if counts["left"] > counts["right"]:
        story.enrichment_direction = "right"
    elif counts["right"] > counts["left"]:
        story.enrichment_direction = "left"
    else:
        story.enrichment_direction = None


def redirect_to_articles(label=None, scrape_status=None):
    next_url = request.form.get("next", "").strip()
    if next_url.startswith("/") and urlparse(next_url).netloc == "":
        return redirect(next_url)
    params = {}
    if label:
        params["topic"] = label
    if scrape_status:
        params["scrape_status"] = scrape_status
    show_single = request.form.get("show_single", "").strip().lower()
    if not show_single and request.referrer:
        referrer = urlparse(request.referrer)
        if referrer.netloc == request.host:
            show_single = parse_qs(referrer.query).get("show_single", [""])[0]
    if show_single == "true":
        params["show_single"] = "true"
    return redirect(url_for("admin.list_articles", **params))


def apply_scrape_result(article, result):
    article.scrape_status = result.status
    article.scrape_method = result.method
    article.scrape_failure_reason = result.failure_reason
    article.scrape_http_status = result.http_status
    article.scrape_audited = False
    if result.content:
        article.content = result.content


@admin.route("/fetch-page")
@login_required
def fetch_page():
    return render_template(
        "fetch.html",
        fetch_presets=FETCH_PRESETS,
        topics=Topic.query.filter_by(is_active=True).order_by(Topic.sort_order).all(),
        active_nav="fetch",
    )


@admin.route("/tools")
@login_required
def tools_page():
    from news_fetcher.fetch_and_store_articles import (
        AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY,
    )
    auto_deep = bool(_load_json_setting(AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY))
    return render_template(
        "admin_tools.html",
        auto_article_deep_analysis_enabled=auto_deep,
    )


@admin.route("/toggle-auto-article-deep-analysis", methods=["POST"])
@login_required
def toggle_auto_article_deep_analysis():
    from news_fetcher.fetch_and_store_articles import (
        AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY,
    )
    enabled = request.form.get("enabled") == "true"
    _save_json_setting(AUTO_ARTICLE_DEEP_ANALYSIS_SETTING_KEY, enabled)
    logger.info(f"[Settings] auto_article_deep_analysis_enabled set to {enabled}")
    return redirect(url_for("admin.tools_page"))


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
        if check_ollama_status():
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


@admin.route("/ollama-catchup", methods=["POST"])
@login_required
def ollama_catchup_route():
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    from news_fetcher.fetch_and_store_articles import ollama_catchup
    _start_bulk_task("ollama_catchup", ollama_catchup)
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
@admin.route("/force-regroup", methods=["POST"])
@login_required
def force_regroup():
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    from news_fetcher.fetch_and_store_articles import force_regroup_all
    _start_bulk_task("force_regroup", force_regroup_all)
    return redirect_to_articles(label, scrape_status)


@admin.route("/force-resummarize", methods=["POST"])
@login_required
def force_resummarize():
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    from news_fetcher.fetch_and_store_articles import force_resummarize_all
    _start_bulk_task("force_resummarize", force_resummarize_all)
    return redirect_to_articles(label, scrape_status)


@admin.route("/wake-ollama", methods=["POST"])
@login_required
def wake_ollama():
    import os
    import wakeonlan
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    try:
        mac = os.environ.get("OLLAMA_MAC", "")
        if mac:
            wakeonlan.send_magic_packet(mac)
    except Exception as e:
        logger.error(f"WoL error: {e}")
    return redirect_to_articles(label, scrape_status)


@admin.route("/reindex-search", methods=["POST"])
@login_required
def reindex_search():
    started_at = datetime.utcnow().isoformat()
    claimed = _try_claim_task_status(
        SEARCH_REINDEX_STATUS_KEY,
        {
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "message": "Reindexing stories and articles into Meilisearch.",
            "story_documents": None,
            "article_documents": None,
        },
    )
    if not claimed:
        return jsonify({
            "started": False,
            "status": _search_reindex_status_payload(),
        }), 409

    app = current_app._get_current_object()
    thread = threading.Thread(target=_run_search_reindex, args=(app,), daemon=True)
    thread.start()

    return jsonify({
        "started": True,
        "status": _search_reindex_status_payload(),
    }), 202


@admin.route("/reindex-search-status")
@login_required
def reindex_search_status():
    return jsonify(_search_reindex_status_payload())


@admin.route("/ollama-status")
@login_required
def ollama_status_detail():
    from news_fetcher.llm_client import llm_status_detail
    return jsonify(llm_status_detail())


@admin.route("/bulk-task-status/<action>")
@login_required
def bulk_task_status(action):
    if action not in BULK_TASK_ACTIONS:
        return jsonify({"status": "error", "message": "Unknown action."}), 404
    return jsonify(_bulk_task_status_payload(action))


@admin.route("/ai-task/start", methods=["POST"])
@login_required
def start_ai_task():
    payload = request.get_json(silent=True) or request.form
    task_type = (payload.get("task_type") or "").strip()
    resource_id = payload.get("resource_id")

    try:
        resource_id = int(resource_id)
    except (TypeError, ValueError):
        return jsonify({
            "started": False,
            "message": "Invalid resource id.",
        }), 400

    if task_type not in {"story_summary", "story_deep_report", "article_summary"}:
        return jsonify({
            "started": False,
            "message": "Invalid AI task type.",
        }), 400

    started_at = datetime.utcnow().isoformat()
    claimed = _try_claim_task_status(
        _ai_task_status_key(task_type, resource_id),
        {
            "task_type": task_type,
            "resource_id": resource_id,
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "message": "AI task is running.",
        },
    )
    if not claimed:
        return jsonify({
            "started": False,
            "status": _ai_task_status_payload(task_type, resource_id),
        }), 409

    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_ai_task,
        args=(app, task_type, resource_id),
        daemon=True,
    )
    thread.start()

    return jsonify({
        "started": True,
        "status": _ai_task_status_payload(task_type, resource_id),
    }), 202


@admin.route("/ai-task-status/<task_type>/<int:resource_id>")
@login_required
def ai_task_status(task_type, resource_id):
    if task_type not in {"story_summary", "story_deep_report", "article_summary"}:
        return jsonify({
            "status": "error",
            "message": "Invalid AI task type.",
        }), 400
    return jsonify(_ai_task_status_payload(task_type, resource_id))


@admin.route("/reclassify-articles", methods=["POST"])
@login_required
def reclassify_articles():
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    from news_fetcher.fetch_and_store_articles import reclassify_all_articles
    _start_bulk_task("reclassify_articles", reclassify_all_articles)
    return redirect_to_articles(label, scrape_status)


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
    cutoff = datetime.utcnow() - timedelta(hours=24)
    entries = ScrapeBlocklist.query.order_by(
        ScrapeBlocklist.is_permanent.desc(),
        ScrapeBlocklist.added_at.desc()
    ).all()
    recent_articles = Article.query.filter(Article.fetched_at >= cutoff).all()

    status_counts = {}
    domain_status_counts = {}
    domain_last_seen = {}

    for article in recent_articles:
        status = (article.scrape_status or "pending").lower()
        status_counts[status] = status_counts.get(status, 0) + 1

        domain = article_domain(article.url)
        if not domain:
            continue

        domain_counts = domain_status_counts.setdefault(domain, {})
        domain_counts[status] = domain_counts.get(status, 0) + 1
        fetched_at = article.fetched_at or article.date
        if fetched_at and fetched_at > domain_last_seen.get(domain, datetime.min):
            domain_last_seen[domain] = fetched_at

    recent_problem_domains = []
    for domain, counts in domain_status_counts.items():
        issue_total = counts.get("blocked", 0) + counts.get("failed", 0) + counts.get("fallback", 0)
        if not issue_total:
            continue
        recent_problem_domains.append({
            "domain": domain,
            "issue_total": issue_total,
            "success": counts.get("success", 0),
            "fallback": counts.get("fallback", 0),
            "blocked": counts.get("blocked", 0),
            "failed": counts.get("failed", 0),
            "skipped": counts.get("skipped", 0),
            "last_seen": domain_last_seen.get(domain),
        })

    recent_problem_domains.sort(
        key=lambda row: (row["issue_total"], row["blocked"], row["failed"], row["fallback"]),
        reverse=True,
    )
    recent_problem_domains = recent_problem_domains[:15]

    retry_cache = _load_json_setting("scrape_retry_cache_v1") or {"domains": {}, "urls": {}}
    retry_cache_domains = []
    retry_cache_urls = []
    for domain, payload in (retry_cache.get("domains") or {}).items():
        if isinstance(payload, dict):
            retry_cache_domains.append({
                "domain": domain,
                "status": payload.get("status"),
                "failure_reason": payload.get("failure_reason"),
                "failure_count": payload.get("failure_count", 0),
                "defer_until": payload.get("defer_until"),
            })
    for url, payload in (retry_cache.get("urls") or {}).items():
        if isinstance(payload, dict):
            retry_cache_urls.append({
                "url": url,
                "status": payload.get("status"),
                "failure_reason": payload.get("failure_reason"),
                "failure_count": payload.get("failure_count", 0),
                "defer_until": payload.get("defer_until"),
            })

    retry_cache_domains.sort(key=lambda row: (row["defer_until"] or "", row["failure_count"]), reverse=True)
    retry_cache_urls.sort(key=lambda row: (row["defer_until"] or "", row["failure_count"]), reverse=True)

    blocklist_rows = []
    for entry in entries:
        counts = domain_status_counts.get(entry.domain, {})
        blocklist_rows.append({
            "entry": entry,
            "success": counts.get("success", 0),
            "fallback": counts.get("fallback", 0),
            "blocked": counts.get("blocked", 0),
            "failed": counts.get("failed", 0),
            "skipped": counts.get("skipped", 0),
            "last_seen": domain_last_seen.get(entry.domain),
        })

    return render_template(
        "scrape_blocklist.html",
        entries=blocklist_rows,
        status_counts=status_counts,
        recent_problem_domains=recent_problem_domains,
        retry_cache_domain_count=len(retry_cache_domains),
        retry_cache_url_count=len(retry_cache_urls),
        retry_cache_domains=retry_cache_domains[:10],
        retry_cache_urls=retry_cache_urls[:10],
        telemetry_window_hours=24,
    )


@admin.route("/audit-scrapes", methods=["POST"])
@login_required
def audit_scrapes():
    from news_fetcher.fetch_and_store_articles import audit_existing_scrapes
    _start_bulk_task("audit_scrapes", audit_existing_scrapes)
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


@admin.route("/sync-allsides", methods=["POST"])
@login_required
def sync_allsides():
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    try:
        from news_fetcher.fetch_and_store_articles import sync_allsides_ratings
        sync_allsides_ratings()
    except Exception as e:
        logger.exception(f"AllSides sync error: {e}")
    return redirect_to_articles(label, scrape_status)


@admin.route("/merge-outlets", methods=["POST"])
@login_required
def merge_outlets():
    from news_fetcher.fetch_and_store_articles import merge_duplicate_outlets
    try:
        summary = merge_duplicate_outlets()
        return jsonify({
            'status': 'ok',
            'renamed': summary['renamed'],
            'outlets_deleted': summary['outlets_deleted'],
            'articles_reassigned': summary['articles_reassigned'],
        })
    except Exception as e:
        logger.error(f"Outlet merge failed: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin.route("/metrics")
@login_required
def metrics():
    return jsonify({
        "last_run_metrics": _load_json_setting("last_run_metrics"),
        "last_headline_site_metrics": _load_json_setting("last_headline_site_metrics"),
        "scrape_outcome_history": _load_json_setting("scrape_outcome_history_v1"),
    })


@admin.route("/topics")
@login_required
def topics_page():
    topics = Topic.query.order_by(Topic.sort_order.asc().nullslast(), Topic.name.asc()).all()
    return render_template("topics.html", topics=topics)


@admin.route("/topics/add", methods=["POST"])
@login_required
def add_topic():
    name = request.form.get("name", "").strip()
    icon = request.form.get("icon", "").strip()[:4] or None
    sort_order = request.form.get("sort_order", type=int)

    if name and not Topic.query.filter_by(name=name).first():
        max_order = db.session.query(func.max(Topic.sort_order)).scalar()
        db.session.add(Topic(
            name=name,
            icon=icon,
            sort_order=sort_order if sort_order is not None else (max_order or 0) + 1,
            is_active=True,
        ))
        db.session.commit()
        logger.info(f"[Topics] Added topic: {name}")
    return redirect(url_for("admin.topics_page"))


@admin.route("/topics/<int:topic_id>/update", methods=["POST"])
@login_required
def update_topic(topic_id):
    topic = Topic.query.get_or_404(topic_id)
    icon = request.form.get("icon", "").strip()[:4]
    sort_order = request.form.get("sort_order", type=int)

    topic.icon = icon or None
    if sort_order is not None:
        topic.sort_order = sort_order
    topic.is_active = request.form.get("is_active") == "on"
    db.session.commit()
    return redirect(url_for("admin.topics_page"))


RSS_FEED_BUCKETS = ["general", "right_enrichment", "left_enrichment"]


@admin.route("/rss-feeds")
@login_required
def rss_feeds_page():
    feeds_by_bucket = {
        bucket: RssFeed.query.filter_by(bucket=bucket).order_by(RssFeed.url.asc()).all()
        for bucket in RSS_FEED_BUCKETS
    }
    return render_template("rss_feeds.html", feeds_by_bucket=feeds_by_bucket, buckets=RSS_FEED_BUCKETS)


@admin.route("/rss-feeds/add", methods=["POST"])
@login_required
def add_rss_feed():
    url = request.form.get("url", "").strip()
    bucket = request.form.get("bucket", "").strip()
    label = request.form.get("label", "").strip() or None

    if url and bucket in RSS_FEED_BUCKETS and not RssFeed.query.filter_by(url=url, bucket=bucket).first():
        db.session.add(RssFeed(url=url, bucket=bucket, label=label, enabled=True))
        db.session.commit()
        logger.info(f"[RssFeeds] Added feed: {url} ({bucket})")
    return redirect(url_for("admin.rss_feeds_page"))


@admin.route("/rss-feeds/<int:feed_id>/update", methods=["POST"])
@login_required
def update_rss_feed(feed_id):
    feed = RssFeed.query.get_or_404(feed_id)
    feed.label = request.form.get("label", "").strip() or None
    feed.enabled = request.form.get("enabled") == "on"
    db.session.commit()
    return redirect(url_for("admin.rss_feeds_page"))


@admin.route("/rss-feeds/<int:feed_id>/delete", methods=["POST"])
@login_required
def delete_rss_feed(feed_id):
    feed = RssFeed.query.get_or_404(feed_id)
    db.session.delete(feed)
    db.session.commit()
    return redirect(url_for("admin.rss_feeds_page"))
