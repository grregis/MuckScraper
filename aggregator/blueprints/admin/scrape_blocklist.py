from datetime import datetime, timedelta
from flask import render_template
from flask_login import login_required
from aggregator.models import Article

from . import admin
from ._shared import _load_json_setting, article_domain


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
