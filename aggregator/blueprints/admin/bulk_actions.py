import logging
import threading
from datetime import datetime
from flask import request, redirect, url_for, jsonify, current_app
from flask_login import login_required
from aggregator import db

from . import admin, SEARCH_REINDEX_STATUS_KEY
from ._shared import redirect_to_articles
from ._tasks import _start_bulk_task, _try_claim_task_status, _run_search_reindex, _search_reindex_status_payload

logger = logging.getLogger(__name__)


@admin.route("/ollama-catchup", methods=["POST"])
@login_required
def ollama_catchup_route():
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    from news_fetcher.fetch_and_store_articles import ollama_catchup
    _start_bulk_task("ollama_catchup", ollama_catchup)
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


@admin.route("/ollama-status")
@login_required
def ollama_status_detail():
    from news_fetcher.llm_client import llm_status_detail
    return jsonify(llm_status_detail())


@admin.route("/reclassify-articles", methods=["POST"])
@login_required
def reclassify_articles():
    label = request.form.get("label", "")
    scrape_status = request.form.get("scrape_status", "").strip() or None
    from news_fetcher.fetch_and_store_articles import reclassify_all_articles
    _start_bulk_task("reclassify_articles", reclassify_all_articles)
    return redirect_to_articles(label, scrape_status)


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
