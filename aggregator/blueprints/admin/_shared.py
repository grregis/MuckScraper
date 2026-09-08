import json
import logging
from urllib.parse import parse_qs, urlparse
from flask import request, redirect, url_for
from aggregator import db
from aggregator.models import AppSetting, ScheduledFetch

logger = logging.getLogger(__name__)


def fetch_presets():
    """
    The one-click preset buttons on the manual fetch page, derived from the
    same ScheduledFetch rows the scheduler runs -- the page offers "the same
    kind of coverage the scheduler uses", so a second hardcoded copy would
    drift the moment anyone edited their fetches.

    Values are coerced to "" rather than left as None: each one is rendered
    straight into a hidden form input, and None would post the string "None".
    """
    rows = ScheduledFetch.query.filter_by(is_active=True).order_by(
        ScheduledFetch.sort_order.asc(), ScheduledFetch.id.asc()
    ).all()
    return [
        {
            "label":          row.label,
            "description":    row.description or "",
            "mode":           row.mode,
            "country":        row.newsapi_country or "",
            "category":       row.newsapi_category or "",
            "query":          row.newsapi_query or "",
            "gnews_query":    row.gnews_query or "",
            "gnews_category": row.gnews_category or "",
        }
        for row in rows
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
