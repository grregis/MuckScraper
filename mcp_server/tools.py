"""Read-only checks behind the MCP tools.

Each function runs inside a Flask app context whose database connection is
read-only (see context.py) and returns a JSON-able dict with a `verdict`
("ok" / "warning" / "problem") and a plain-language `summary` where a
judgment applies. None of these send anything to an LLM, wake the Ollama box,
or change a row.
"""
import glob
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timedelta

import requests
from sqlalchemy import text

from aggregator import db
from mcp_server import verdicts as v
from mcp_server.context import proxy_get, repo_root

FETCH_RUN_STATUS_KEY = "fetch_run_status_v1"
HISTORY_KEY = "scrape_outcome_history_v1"
LEFT_MAX, CENTER_MAX = 2.5, 3.5  # same thresholds as story_bias_totals()
BIAS_RETRY_CEILING = 15          # news_fetcher/fetch_and_store_articles/outlets.py
LIST_CAP = 25


def _now():
    return datetime.utcnow()


def _q(sql, **params):
    return db.session.execute(text(sql), params)


def _setting(key):
    row = _q("select value from app_settings where key = :k", k=key).fetchone()
    if not row or row[0] is None:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return row[0]


def _iso(dt):
    return dt.isoformat(timespec="seconds") if dt else None


def _bias_side(score):
    if score is None:
        return "unrated"
    if score <= LEFT_MAX:
        return "left"
    if score <= CENTER_MAX:
        return "center"
    return "right"


def _schedule_rows():
    return [(r.hour, bool(r.run_full_pipeline)) for r in
            _q("select hour, run_full_pipeline from pipeline_schedule where is_active").fetchall()]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def run_status():
    now = _now()
    status = _setting(FETCH_RUN_STATUS_KEY) or {}
    running, stale = v.run_in_progress(status, now)
    rows = _schedule_rows()
    last_fetch = v.parse_iso(_setting("last_fetch"))
    slot = v.latest_slot_before(rows, now)
    try:
        from aggregator.blueprints.admin.tools import _running_operations
        # The scheduled run itself is reported separately above.
        others = [op["label"] for op in _running_operations() if not op["label"].startswith("Scheduled ")]
    except Exception:
        others = []
    level, summary = v.run_status_verdict(running, stale, status, last_fetch, slot, others, now)
    return {
        "verdict": level,
        "summary": summary,
        "run_in_progress": running,
        "run_status_flag": status,
        "last_completed_fetch_utc": _iso(last_fetch),
        "scheduler_started_at_utc": _setting("scheduler_started_at"),
        "next_runs": v.upcoming_runs(rows, now),
        "schedule_timezone": v.SCHEDULE_TIMEZONE,
        "now_utc": _iso(now),
    }


def last_run():
    m = _setting("last_run_metrics") or {}
    report = _setting("last_fetch_report") or {}
    totals = m.get("totals") or {}
    steps = m.get("steps") or {}
    started, finished = v.parse_iso(m.get("started_at")), v.parse_iso(m.get("finished_at"))
    step_status = {name: (s.get("status") if isinstance(s, dict) else s) for name, s in steps.items()}
    failed = {n: s for n, s in step_status.items() if s in ("error", "failed")}
    full = not any(isinstance(s, dict) and s.get("reason") == "fetch_only_run" for s in steps.values())
    ollama = report.get("ollama") or {}
    level = v.OK
    notes = []
    if m.get("status") not in (None, "ok"):
        level = v.PROBLEM
        notes.append(f"Run status is {m.get('status')}.")
    if failed:
        level = v.PROBLEM
        notes.append("Failed steps: " + ", ".join(sorted(failed)) + ".")
    if ollama and not ollama.get("up_at_start"):
        level = v.PROBLEM if full else level
        notes.append("Ollama was down at the start of the run" + (" (no LLM work happened)." if full else "."))
    if ollama.get("went_down_during_run") or ollama.get("inference_wedged"):
        level = v.PROBLEM
        notes.append("Ollama dropped or was wedged during the run; check outage_check for articles left without embeddings.")
    if not notes:
        notes.append("Last run completed normally.")
    return {
        "verdict": level,
        "summary": " ".join(notes),
        "type": "full" if full else "fetch-only",
        "status": m.get("status"),
        "started_at_utc": m.get("started_at"),
        "finished_at_utc": m.get("finished_at"),
        "duration_min": round((finished - started).total_seconds() / 60, 1) if started and finished else None,
        "input_articles": totals.get("input_articles"),
        "stored_articles": totals.get("stored"),
        "stories_touched": totals.get("stories_touched"),
        "new_outlets": totals.get("new_outlets"),
        "skipped_at_ingestion": totals.get("skipped"),
        "scrape_statuses": totals.get("scrape_statuses"),
        "steps": step_status,
        "ollama": {k: ollama.get(k) for k in ("up_at_start", "up_at_end", "went_down_during_run", "inference_wedged")},
        "edition": (report.get("edition") or {}),
    }


def run_history(runs=10):
    runs = max(1, min(int(runs), 40))
    history = _setting(HISTORY_KEY) or []
    entries = []
    for h in history[-runs:]:
        q = h.get("quality") or {}
        entries.append({
            "finished_at": h.get("recorded_at"),
            "status": h.get("status"),
            "duration_min": round(h["duration_seconds"] / 60, 1) if h.get("duration_seconds") else None,
            "input": h.get("input_articles"),
            "stored": h.get("stored_articles"),
            "edition_id": (h.get("edition") or {}).get("id"),
            "headlines_generated": (q.get("headline_generation") or {}).get("generated") if q.get("headline_generation") else None,
            "story_summaries": (q.get("edition_content") or {}).get("story_summaries_generated") if q.get("edition_content") else None,
        })
    return {"runs": entries, **v.history_flags(entries),
            "note": "None means the phase did not run (fetch-only runs skip LLM phases), not zero."}


LOG_KINDS = {
    "errors": ("[ERROR]", "Traceback", "[CRITICAL]"),
    "warnings": ("[WARNING]",),
    "guards": ("Rejected story summary", "Rejected non-headline", "Fixed acronym casing"),
    "dedupe": ("Skipping same-event duplicate",),
    "edition": ("[Edition]",),
    "ollama": ("Ollama", "[n8n]", "inference", "wedged", "timed out", "Timeout"),
    "search": ("[Search]",),
    "runs": ("=== Starting scheduled fetch run", "fetch run complete", "Scheduler starting", "missed scheduled run"),
}
# Known noise that is not an error (CLAUDE.md / review notes).
LOG_NOISE = ("error getting summary:", "Document is empty")


def scheduler_log(kind="errors", minutes=180, max_lines=100, container="scheduler"):
    if kind != "all" and kind not in LOG_KINDS:
        return {"error": f"unknown kind; use one of: all, {', '.join(LOG_KINDS)}"}
    if container not in ("scheduler", "app"):
        return {"error": "container must be scheduler or app"}
    minutes = max(1, min(int(minutes), 7 * 24 * 60))
    max_lines = max(1, min(int(max_lines), 300))
    data = proxy_get(f"/containers/{container}/logs", since_minutes=minutes, tail=5000)
    if "error" in data:
        return data
    lines = data.get("lines") or []
    if kind != "all":
        needles = LOG_KINDS[kind]
        lines = [l for l in lines if any(n in l for n in needles)]
        if kind in ("errors", "warnings"):
            lines = [l for l in lines if not any(n in l for n in LOG_NOISE)]
    total = len(lines)
    lines = [l[:400] for l in lines[-max_lines:]]
    return {"container": container, "kind": kind, "minutes": minutes,
            "matching_lines": total, "returned": len(lines), "lines": lines}


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def _probe_ollama(host):
    if not host:
        return {"result": "not_configured"}
    base = host.rstrip("/")
    try:
        r = requests.get(f"{base}/api/version", timeout=(3, 5))
        version = r.json().get("version")
    except requests.exceptions.ConnectTimeout:
        return {"result": "timeout"}
    except requests.exceptions.ReadTimeout:
        return {"result": "hung"}
    except requests.exceptions.ConnectionError as e:
        msg = str(e).lower()
        if "refused" in msg:
            return {"result": "refused"}
        if "no route" in msg or "unreachable" in msg or "timed out" in msg:
            return {"result": "timeout"}
        return {"result": "error", "error": str(e)[:200]}
    except Exception as e:
        return {"result": "error", "error": str(e)[:200]}
    models = []
    try:
        ps = requests.get(f"{base}/api/ps", timeout=(3, 5)).json()
        models = [{"name": m.get("name"), "size": m.get("size"), "size_vram": m.get("size_vram"),
                   "context_length": m.get("context_length"), "expires_at": m.get("expires_at")}
                  for m in ps.get("models") or []]
    except Exception:
        pass
    return {"result": "ok", "version": version, "models": models}


def ollama_health():
    host = os.environ.get("OLLAMA_HOST", "")
    fallback = os.environ.get("OLLAMA_FALLBACK_HOST", "")
    probe = _probe_ollama(host)
    report = _setting("last_fetch_report") or {}
    last_finished = v.parse_iso((_setting("last_run_metrics") or {}).get("finished_at"))
    suspend_after = False
    logs = proxy_get("/containers/scheduler/logs", since_minutes=24 * 60, tail=5000)
    last_webhook = None
    for line in logs.get("lines") or []:
        if "[n8n] Webhook fired" in line:
            last_webhook = line[:19]
    if last_webhook and last_finished:
        wh = v.parse_iso(last_webhook.replace(" ", "T"))
        suspend_after = bool(wh and wh >= last_finished - timedelta(minutes=5))
    expected_ctx = v.parse_expected_contexts(os.environ.get("MCP_EXPECTED_OLLAMA_CONTEXT", ""))
    level, summary = v.ollama_verdict(probe, report.get("ollama"), suspend_after, expected_ctx)
    out = {
        "verdict": level,
        "summary": summary,
        "primary": {"result": probe.get("result"), "version": probe.get("version"),
                    "loaded_models": probe.get("models")},
        "last_run_ollama_state": {k: (report.get("ollama") or {}).get(k)
                                  for k in ("up_at_start", "up_at_end", "went_down_during_run", "inference_wedged")},
        "last_suspend_webhook": last_webhook,
        "models_configured": {"OLLAMA_MODEL": os.environ.get("OLLAMA_MODEL"),
                              "OLLAMA_FAST_MODEL": os.environ.get("OLLAMA_FAST_MODEL") or None,
                              "EMBEDDING_MODEL": os.environ.get("EMBEDDING_MODEL")},
        "note": "Read-only: no inference call is made and the box is never woken.",
    }
    if fallback:
        out["fallback"] = {"result": _probe_ollama(fallback).get("result")}
    probe_log = os.environ.get("OLLAMA_PROBE_LOG", "")
    if probe_log and os.path.exists(probe_log):
        with open(probe_log, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 4000))
            out["probe_log_tail"] = fh.read().decode("utf-8", "replace").splitlines()[-15:]
    return out


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------

def edition(edition_id=None):
    if edition_id is None:
        row = _q("select id from editions where published order by created_at desc limit 1").fetchone()
        if not row:
            return {"error": "no published edition"}
        edition_id = row[0]
    ed = _q("select id, date, edition_type, created_at, published from editions where id = :i",
            i=int(edition_id)).fetchone()
    if not ed:
        return {"error": f"edition {edition_id} not found"}
    rows = _q("""
        select es.rank, es.has_updates, s.id, coalesce(nullif(s.headline, ''), s.title) headline,
               s.summary is not null and s.summary <> '' has_summary,
               s.deep_report is not null and s.deep_report <> '' has_report,
               count(a.id) articles,
               count(distinct coalesce(a.outlet_id::text, lower(a.source))) outlets,
               sum(case when coalesce(a.bias_score, o.bias_score) <= :l then 1 else 0 end) left_n,
               sum(case when coalesce(a.bias_score, o.bias_score) > :l and coalesce(a.bias_score, o.bias_score) <= :c then 1 else 0 end) center_n,
               sum(case when coalesce(a.bias_score, o.bias_score) > :c then 1 else 0 end) right_n,
               string_agg(distinct a.source, ', ') sources
        from edition_stories es join stories s on s.id = es.story_id
        left join articles a on a.story_id = s.id left join outlets o on o.id = a.outlet_id
        where es.edition_id = :e group by es.rank, es.has_updates, s.id order by es.rank""",
              e=ed.id, l=LEFT_MAX, c=CENTER_MAX).fetchall()
    stories = []
    for r in rows:
        stories.append({
            "rank": r.rank, "story_id": r.id, "headline": r.headline, "articles": r.articles,
            "outlets": r.outlets, "bias": {"left": r.left_n, "center": r.center_n, "right": r.right_n},
            "sources": (r.sources or "")[:300], "has_summary": r.has_summary,
            "has_deep_report": r.has_report, "repeat_with_updates": r.has_updates,
            "flags": [f for f, on in (("one_outlet", r.outlets < 2), ("no_summary", not r.has_summary),
                                      ("empty_story", r.articles == 0)) if on],
        })
    one_outlet = [s["rank"] for s in stories if "one_outlet" in s["flags"]]
    notes = []
    level = v.OK
    if len(stories) < 20:
        level = v.WARNING
        notes.append(f"Only {len(stories)} stories (target 20).")
    if any(r <= 10 for r in one_outlet):
        level = v.WARNING
        notes.append("A single-outlet story is in the top 10: ranks " + ", ".join(map(str, one_outlet)) + ".")
    elif one_outlet:
        notes.append("Single-outlet stories at ranks " + ", ".join(map(str, one_outlet)) + " (fill; usually from the bias-balance minimums).")
    missing = [s["rank"] for s in stories if not s["has_summary"]]
    if missing:
        level = v.WARNING
        notes.append("No summary at ranks " + ", ".join(map(str, missing)) + ".")
    return {
        "verdict": level, "summary": " ".join(notes) or "Edition looks complete.",
        "edition": {"id": ed.id, "date": str(ed.date), "type": ed.edition_type,
                    "published_at_utc": _iso(ed.created_at), "published": ed.published},
        "stories": stories,
    }


def outage_check(hours=24):
    hours = max(1, min(int(hours), 24 * 14))
    since = _now() - timedelta(hours=hours)
    r = _q("""
        select count(*) total,
               count(*) filter (where a.embedding is null) no_embedding,
               count(*) filter (where not exists (
                   select 1 from article_topics at join topics t on t.id = at.topic_id
                   where at.article_id = a.id and t.name <> 'Other')) other_only,
               count(*) filter (where (select count(*) from articles b where b.story_id = a.story_id) = 1) singletons
        from articles a where a.fetched_at >= :s""", s=since).fetchone()
    base = _q("""
        select avg(case when n = 1 then 1.0 else 0 end) from (
            select (select count(*) from articles b where b.story_id = a.story_id) n
            from articles a where a.fetched_at >= :s) x""", s=_now() - timedelta(days=7)).scalar()
    share = (r.singletons / r.total) if r.total else 0.0
    level, summary = v.outage_verdict(r.total, r.no_embedding, r.other_only, share, float(base or 0))
    first_missing = _q("""select min(fetched_at), max(fetched_at) from articles
                          where fetched_at >= :s and embedding is null""", s=since).fetchone()
    return {
        "verdict": level, "summary": summary, "window_hours": hours,
        "articles": r.total, "without_embedding": r.no_embedding, "topic_other_only": r.other_only,
        "singleton_share": round(share, 3), "singleton_share_7d": round(float(base or 0), 3),
        "missing_embedding_fetched_between_utc": [_iso(first_missing[0]), _iso(first_missing[1])] if r.no_embedding else None,
    }


def quality_report(since=None):
    from news_fetcher.quality_report import collect
    rep = collect(since, samples=0, langfuse=False)

    def trim(obj):
        if isinstance(obj, list):
            return [trim(x) for x in obj[:10]]
        if isinstance(obj, dict):
            return {k: trim(val) for k, val in obj.items()}
        return obj

    rep.pop("langfuse", None)
    return trim(rep)


def get_story(story_id):
    s = _q("""select id, title, headline, summary, deep_report, created_at, headline_generated_at,
                     summary_generated_at, headline_score from stories where id = :i""", i=int(story_id)).fetchone()
    if not s:
        return {"error": f"story {story_id} not found"}
    arts = _q("""select a.id, a.source, a.title, a.date, a.scrape_status, length(a.content) content_len,
                        coalesce(a.bias_score, o.bias_score) bias, a.grouping_match_method
                 from articles a left join outlets o on o.id = a.outlet_id
                 where a.story_id = :i order by a.date desc limit 60""", i=s.id).fetchall()
    topics = [r[0] for r in _q("""select t.name from story_topics st join topics t on t.id = st.topic_id
                                   where st.story_id = :i""", i=s.id).fetchall()]
    eds = _q("""select e.id, e.date, e.edition_type, es.rank from edition_stories es
                join editions e on e.id = es.edition_id where es.story_id = :i order by e.created_at""", i=s.id).fetchall()
    return {
        "story_id": s.id, "headline": s.headline, "title": s.title, "topics": topics,
        "created_at_utc": _iso(s.created_at), "headline_generated_at_utc": _iso(s.headline_generated_at),
        "summary_generated_at_utc": _iso(s.summary_generated_at), "headline_score": s.headline_score,
        "summary": (s.summary or "")[:2000] or None,
        "deep_report": (s.deep_report or "")[:3000] or None,
        "article_count": len(arts),
        "outlets": len({a.source for a in arts}),
        "articles": [{"id": a.id, "source": a.source, "title": a.title, "published_utc": _iso(a.date),
                      "bias": _bias_side(a.bias), "scrape_status": a.scrape_status,
                      "content_chars": a.content_len or 0, "grouped_by": a.grouping_match_method} for a in arts],
        "editions": [{"edition_id": e.id, "date": str(e.date), "type": e.edition_type, "rank": e.rank} for e in eds],
    }


def get_article(article_id):
    a = _q("""select a.id, a.title, a.source, a.url, a.date, a.fetched_at, a.story_id, a.scrape_status,
                     a.scrape_method, a.scrape_failure_reason, a.scrape_http_status, length(a.content) content_len,
                     left(a.content, 600) preview, a.summary, coalesce(a.bias_score, o.bias_score) bias,
                     o.name outlet, o.bias_source, a.grouping_match_method, a.grouping_confidence,
                     a.embedding is not null has_embedding
              from articles a left join outlets o on o.id = a.outlet_id where a.id = :i""", i=int(article_id)).fetchone()
    if not a:
        return {"error": f"article {article_id} not found"}
    topics = [r[0] for r in _q("""select t.name from article_topics at join topics t on t.id = at.topic_id
                                   where at.article_id = :i""", i=a.id).fetchall()]
    return {
        "article_id": a.id, "title": a.title, "source": a.source, "outlet": a.outlet, "url": a.url,
        "published_utc": _iso(a.date), "fetched_utc": _iso(a.fetched_at), "story_id": a.story_id,
        "topics": topics, "bias_score": a.bias, "bias_side": _bias_side(a.bias), "bias_source": a.bias_source,
        "scrape": {"status": a.scrape_status, "method": a.scrape_method, "failure_reason": a.scrape_failure_reason,
                   "http_status": a.scrape_http_status, "content_chars": a.content_len or 0},
        "content_preview": a.preview, "summary": a.summary,
        "grouping": {"method": a.grouping_match_method, "confidence": a.grouping_confidence,
                     "note": "confidence scales differ by method; compare only within one method"},
        "has_embedding": a.has_embedding,
    }


def search(query, kind="stories", days=7, limit=20):
    limit = max(1, min(int(limit), 50))
    days = int(days) if days else None
    since = _now() - timedelta(days=days) if days else None
    used = "meilisearch"
    ids = None
    try:
        from aggregator import search as s
        if s.meili_enabled():
            fn = s.search_story_ids if kind == "stories" else s.search_article_ids
            ids = fn(query, limit=limit, since=since)
    except Exception:
        ids = None
    if kind == "stories":
        if ids is None:
            used = "sql substring (Meilisearch unavailable)"
            ids = [r[0] for r in _q("""select id from stories where (headline ilike :p or title ilike :p)
                                        and (cast(:s as timestamp) is null or created_at >= :s)
                                        order by created_at desc limit :n""",
                                     p=f"%{query}%", s=since, n=limit).fetchall()]
        rows = _q("""select s.id, coalesce(nullif(s.headline, ''), s.title) h, count(a.id) n, max(a.date) last
                     from stories s left join articles a on a.story_id = s.id where s.id = any(:i)
                     group by s.id""", i=list(ids)).fetchall()
        order = {sid: n for n, sid in enumerate(ids)}
        results = sorted(({"story_id": r.id, "headline": r.h, "articles": r.n, "latest_article_utc": _iso(r.last)}
                          for r in rows), key=lambda x: order.get(x["story_id"], 0))
    else:
        if ids is None:
            used = "sql substring (Meilisearch unavailable)"
            ids = [r[0] for r in _q("""select id from articles where title ilike :p
                                        and (cast(:s as timestamp) is null or date >= :s)
                                        order by date desc limit :n""", p=f"%{query}%", s=since, n=limit).fetchall()]
        rows = _q("select id, title, source, date, story_id from articles where id = any(:i)", i=list(ids)).fetchall()
        order = {aid: n for n, aid in enumerate(ids)}
        results = sorted(({"article_id": r.id, "title": r.title, "source": r.source,
                           "published_utc": _iso(r.date), "story_id": r.story_id} for r in rows),
                         key=lambda x: order.get(x["article_id"], 0))
    return {"query": query, "kind": kind, "days": days, "engine": used, "results": results[:limit]}


def scrape_health(hours=24):
    hours = max(1, min(int(hours), 24 * 14))
    since = _now() - timedelta(hours=hours)
    statuses = dict(_q("""select scrape_status, count(*) from articles where fetched_at >= :s
                          group by 1""", s=since).fetchall())
    bad = _q("""select regexp_replace(url, '^https?://(www\\.)?([^/]+).*', '\\2') d, scrape_status, count(*) n
                from articles where fetched_at >= :s and scrape_status in ('blocked', 'failed')
                group by 1, 2 order by 3 desc limit :n""", s=since, n=LIST_CAP).fetchall()
    total = sum(statuses.values())
    failing = statuses.get("blocked", 0) + statuses.get("failed", 0)
    blocklist = _q("""select count(*), count(*) filter (where added_at >= :s) from scrape_blocklist""", s=since).fetchone()
    by_domain = [{"domain": r.d, "status": r.scrape_status, "count": r.n} for r in bad]
    level, summary = v.OK, "Scrape outcomes look normal."
    if total and failing / total > 0.25:
        top = by_domain[0] if by_domain else None
        level = v.WARNING
        summary = (f"{failing} of {total} articles blocked or failed. "
                   + (f"Largest single domain: {top['domain']} ({top['count']}). " if top else "")
                   + "Check whether one domain explains it before calling it a trend.")
    return {"verdict": level, "summary": summary, "window_hours": hours, "by_status": statuses,
            "blocked_or_failed_by_domain": by_domain,
            "scrape_blocklist": {"domains": blocklist[0], "added_in_window": blocklist[1]}}


def bias_coverage():
    r = _q("""select count(*) total,
                     count(*) filter (where bias_score is null) unrated,
                     count(*) filter (where bias_score is null and bias_retry_count >= :c) abandoned,
                     count(*) filter (where bias_source = 'allsides') allsides,
                     count(*) filter (where bias_source = 'ai') ai
              from outlets""", c=BIAS_RETRY_CEILING).fetchone()
    recent = _q("""select o.name, count(a.id) n from outlets o join articles a on a.outlet_id = o.id
                   where o.bias_score is null and a.fetched_at >= :s group by o.name order by n desc limit 15""",
                s=_now() - timedelta(days=7)).fetchall()
    return {
        "outlets": r.total, "rated_by_allsides": r.allsides, "rated_by_model": r.ai, "unrated": r.unrated,
        "unrated_at_retry_ceiling": r.abandoned,
        "unrated_outlets_with_articles_last_7d": [{"outlet": x.name, "articles": x.n} for x in recent],
        "note": "Article bias is a copy of its outlet's rating, so outlet coverage is article coverage. "
                f"Outlets at {BIAS_RETRY_CEILING} failed rating attempts are no longer retried.",
    }


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

def _containers():
    data = proxy_get("/containers")
    return data if isinstance(data, list) else []


def containers():
    rows = _containers()
    disk = None
    try:
        st = os.statvfs(repo_root())
        disk = {"free_gb": round(st.f_bavail * st.f_frsize / 1e9, 1),
                "total_gb": round(st.f_blocks * st.f_frsize / 1e9, 1)}
    except OSError:
        pass
    db_size = _q("select pg_size_pretty(pg_database_size(current_database()))").scalar()
    down = [c["service"] for c in rows if c.get("status") != "running"]
    level = v.PROBLEM if down else v.OK
    summary = ("Not running: " + ", ".join(down) + ".") if down else "All containers are running."
    if not rows:
        level, summary = v.WARNING, "Could not list containers (docker-restart-proxy unreachable)."
    if disk and disk["total_gb"] and disk["free_gb"] / disk["total_gb"] < 0.1:
        level = v.WARNING if level == v.OK else level
        summary += f" Disk is nearly full ({disk['free_gb']} GB free)."
    return {"verdict": level, "summary": summary, "containers": rows, "disk": disk, "database_size": db_size}


def search_health():
    from aggregator import search as s
    if not s.meili_enabled():
        return {"verdict": v.OK, "summary": "Meilisearch is not configured (MEILI_URL unset)."}
    healthy = s.healthcheck()
    out = {"meilisearch_up": healthy}
    counts = {}
    if healthy:
        for idx in (s.STORY_INDEX, s.ARTICLE_INDEX):
            try:
                counts[idx] = s._request("GET", f"/indexes/{idx}/stats").get("numberOfDocuments")
            except Exception:
                counts[idx] = None
    db_counts = {"stories": _q("select count(*) from stories").scalar(),
                 "articles": _q("select count(*) from articles").scalar()}
    step = ((_setting("last_run_metrics") or {}).get("steps") or {}).get("search_index")
    out.update({"documents": counts, "database_rows": db_counts, "last_run_search_update": step})
    level, notes = v.OK, []
    if not healthy:
        level = v.PROBLEM
        notes.append("Meilisearch is not answering; search falls back to slow SQL matching.")
    for idx, n in counts.items():
        rows = db_counts.get(idx)
        if n is not None and rows and abs(n - rows) / rows > 0.02:
            level = v.WARNING if level == v.OK else level
            notes.append(f"Index '{idx}' has {n} documents vs {rows} rows; a full rebuild may be needed.")
    if isinstance(step, dict) and step.get("status") == "error":
        level = v.WARNING if level == v.OK else level
        notes.append("The last run's search update failed: " + str(step.get("reason"))[:200])
    out["verdict"], out["summary"] = level, " ".join(notes) or "Search index is up and in step with the database."
    return out


def _git(*args):
    return subprocess.run(["git", "-C", repo_root(), *args], capture_output=True, text=True, timeout=20).stdout.strip()


def code_live_check():
    root = repo_root()
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    head = _git("log", "-1", "--format=%h %cI %s")
    commit_ts = _git("log", "-1", "--format=%ct", "--", "aggregator", "news_fetcher")
    newest = datetime.utcfromtimestamp(int(commit_ts)) if commit_ts.isdigit() else None
    dirty = [l[3:] for l in _git("status", "--porcelain").splitlines() if l[:2].strip() and l[3:].endswith(".py")]
    for path in dirty:
        full = os.path.join(root, path)
        if os.path.isfile(full) and path.split("/")[0] in ("aggregator", "news_fetcher"):
            mtime = datetime.utcfromtimestamp(os.path.getmtime(full))
            newest = max(newest, mtime) if newest else mtime
    started = {}
    for c in _containers():
        if c.get("service") in ("app", "scheduler"):
            started[c["service"]] = v.parse_iso((c.get("started_at") or "")[:26] + "+00:00"
                                                if c.get("started_at") else None)
    verdicts = v.code_live_verdict(started, newest)
    stale = [k for k, msg in verdicts.items() if msg.startswith("STALE")]
    return {
        "verdict": v.WARNING if stale else v.OK,
        "summary": ("Running code is older than the code on disk for: " + ", ".join(stale) + ".") if stale
                   else "Running containers have the newest code loaded.",
        "branch": branch, "head": head,
        "uncommitted_python_files": dirty[:LIST_CAP],
        "newest_code_change_utc": _iso(newest),
        "containers": verdicts,
        "note": "The scheduler imports its modules once at startup; a change on disk is not live until a restart.",
    }


# ---------------------------------------------------------------------------
# Runbook
# ---------------------------------------------------------------------------

DEFAULT_RUNBOOK_FILES = "docs/MCP.md,TROUBLESHOOTING.md,TROUBLESHOOTINGHL.md,CLAUDE.md,README.md"


def _runbook_sections():
    sections = []
    for name in os.environ.get("MCP_RUNBOOK_FILES", DEFAULT_RUNBOOK_FILES).split(","):
        name = name.strip()
        for path in glob.glob(os.path.join(repo_root(), name)):
            try:
                with open(path, encoding="utf-8") as fh:
                    sections.extend(v.split_sections(fh.read(), os.path.relpath(path, repo_root())))
            except OSError:
                continue
    return sections


def runbook(topic=None):
    sections = _runbook_sections()
    if not sections:
        return {"error": "no runbook files found (MCP_RUNBOOK_FILES)"}
    if not topic:
        counts = Counter(s["source"] for s in sections)
        return {"sources": dict(counts),
                "titles": [f"{s['source']}: {s['title'][:90]}" for s in sections][:200],
                "hint": "Call again with topic='...' (e.g. 'ollama unreachable', 'restart scheduler')."}
    hits = v.search_sections(sections, topic)
    if not hits:
        return {"topic": topic, "results": [], "hint": "No match; call runbook() with no topic to list sections."}
    return {"topic": topic, "results": [{"source": h["source"], "title": h["title"],
                                         "text": h["text"][:4000]} for h in hits]}
