"""Read-only quality evidence pack for a fetch run.

    python3 -m news_fetcher.quality_report [--since ISO] [--json] [--samples N]

Gathers deterministic findings about headlines, summaries/analyses, grouping,
bias ratings and the published edition, plus sampled Langfuse prompt/output
pairs for the flagged items. It never writes to the database. Judgment (is this
headline actually wrong?) is left to whoever reads the pack -- the detectors
here only surface candidates.

Every section is collected independently, so one failing query is reported in
place ("error") instead of losing the whole pack.
"""
import argparse
import difflib
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta

from news_fetcher import quality_checks as qc

logger = logging.getLogger(__name__)

MAX_FINDINGS = 25          # per finding list; counts are always exact
BODY_CHARS = 3000          # source text per article used for number/entity checks
NEAR_DUP_BUDGET_SECONDS = 20


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _load_setting(key):
    from aggregator.models import AppSetting
    row = AppSetting.query.filter_by(key=key).first()
    if not row or not row.value:
        return None
    try:
        return json.loads(row.value)
    except ValueError:
        return None


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _cap(items):
    return items[:MAX_FINDINGS]


def _safe(name, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # a broken section must not sink the pack
        logger.exception("quality_report section %s failed", name)
        try:
            from aggregator import db
            db.session.rollback()
        except Exception:
            pass
        return {"error": f"{type(exc).__name__}: {exc}"}


def _source_texts(story):
    texts = []
    for article in story.articles:
        texts.append(article.title or "")
        if article.content:
            texts.append(article.content[:BODY_CHARS])
    return texts


def _fold_name(name):
    name = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    name = re.sub(r"\b(the|news|network|online|com)\b", " ", name)
    return re.sub(r"\s+", " ", name).strip()


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def resolve_scope(since_arg):
    """Window start and the run it corresponds to."""
    last = _load_setting("last_run_metrics") or {}
    run_start = _parse_iso(last.get("started_at"))
    since = _parse_iso(since_arg) if since_arg else None
    basis = "--since"
    if since is None:
        since = run_start
        basis = "last run start"
    if since is None:
        since = datetime.utcnow() - timedelta(hours=24)
        basis = "24h fallback"
    steps = last.get("steps") or {}
    full = not any(
        isinstance(s, dict) and s.get("reason") == "fetch_only_run" for s in steps.values()
    )
    return {
        "since": since.isoformat(),
        "since_basis": basis,
        "last_run": {
            "status": last.get("status"),
            "started_at": last.get("started_at"),
            "finished_at": last.get("finished_at"),
            "full_pipeline": full if steps else None,
        },
        "steps": {
            name: (s.get("status") if isinstance(s, dict) else None)
            for name, s in steps.items()
            if isinstance(s, dict) and s.get("status") not in (None, "ok", "skipped")
        },
    }, since


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------

def collect_completeness(since):
    from aggregator import db
    from aggregator.models import Article, Story
    from news_fetcher.headline_generator import _stale_headline_query

    week = datetime.utcnow() - timedelta(days=7)
    article_counts = (
        db.session.query(Article.story_id.label("sid"), db.func.count(Article.id).label("n"))
        .filter(Article.story_id.isnot(None))
        .group_by(Article.story_id)
        .subquery()
    )
    multi_no_headline = (
        db.session.query(Story)
        .join(article_counts, article_counts.c.sid == Story.id)
        .filter(article_counts.c.n >= 2, Story.created_at >= week)
        .filter((Story.headline.is_(None)) | (Story.headline == ""))
        .count()
    )
    no_embedding_7d = Article.query.filter(
        Article.fetched_at >= week, Article.embedding.is_(None)
    ).count()
    no_topic_window = (
        Article.query.filter(Article.fetched_at >= since)
        .filter(~Article.topics.any())
        .count()
    )
    return {
        "multi_article_stories_without_headline_7d": multi_no_headline,
        "stale_headline_candidates_now": _stale_headline_query().count(),
        "articles_without_embedding_7d": no_embedding_7d,
        "articles_without_topic_in_window": no_topic_window,
    }


# ---------------------------------------------------------------------------
# Headlines
# ---------------------------------------------------------------------------

def _headline_findings(story):
    headline = (story.headline or "").strip()
    found = []
    from news_fetcher.headline_generator import _looks_like_llm_failure

    if _looks_like_llm_failure(story.headline):
        found.append("llm_failure_text")
    if "\n" in (story.headline or "").strip():
        found.append("multiline")
    if qc.headline_looks_truncated(headline):
        found.append("truncated")
    if qc.acronym_cased_tokens(headline):
        found.append("acronym_casing")
    if len(headline.split()) > 20:
        found.append("too_long")
    return found


def collect_headlines(since, samples):
    from aggregator import db
    from aggregator.models import Story

    stories = Story.query.filter(Story.headline.isnot(None), Story.headline != "").all()
    window = [s for s in stories if s.headline_generated_at and s.headline_generated_at >= since]

    counts = Counter()
    findings = []
    for story in stories:
        kinds = _headline_findings(story)
        for kind in kinds:
            counts[kind] += 1
        if kinds:
            findings.append({
                "story_id": story.id,
                "kinds": kinds,
                "headline": story.headline,
                "acronyms": qc.acronym_cased_tokens(story.headline),
                "generated_in_window": bool(
                    story.headline_generated_at and story.headline_generated_at >= since
                ),
            })

    # Number checks are the expensive one (they load article bodies), so they
    # only run over headlines written in the window.
    number_findings = []
    for story in window:
        if len(story.articles) < 2:
            continue
        bad = qc.unsupported_numbers(story.headline, _source_texts(story))
        if bad:
            number_findings.append({
                "story_id": story.id,
                "headline": story.headline,
                "unsupported_values": bad,
                "source_titles": [a.title for a in story.articles[:5]],
            })
    counts["unsupported_number"] = len(number_findings)

    return {
        "headlines_total": len(stories),
        "headlines_generated_in_window": len(window),
        "counts_all_time": dict(counts),
        "findings": _cap(sorted(findings, key=lambda f: not f["generated_in_window"])),
        "unsupported_numbers": _cap(number_findings),
        "note": (
            "counts_all_time covers every stored headline, so it includes defects the "
            "pipeline has since stopped producing; generated_in_window separates them. "
            "unsupported_numbers catches magnitude invention only, not misread figures."
        ),
    }


# ---------------------------------------------------------------------------
# Summaries and analysis
# ---------------------------------------------------------------------------

def _editions_in_scope(since):
    from aggregator.models import Edition

    editions = (
        Edition.query.filter(Edition.published == True, Edition.created_at >= since)  # noqa: E712
        .order_by(Edition.created_at.asc())
        .all()
    )
    if not editions:
        latest = (
            Edition.query.filter(Edition.published == True)  # noqa: E712
            .order_by(Edition.created_at.desc())
            .first()
        )
        editions = [latest] if latest else []
    return editions


def _deep_report_prompts():
    from aggregator.models import PromptTemplate

    return {
        p.key.split(".", 1)[1]: p
        for p in PromptTemplate.query.filter(PromptTemplate.key.like("deep_report.%")).all()
    }


def collect_summaries(since, samples):
    from aggregator.models import EditionStory
    from news_fetcher.summarizer import detect_analysis_type

    prompts = _deep_report_prompts()
    editions = _editions_in_scope(since)
    seen = set()
    counts = Counter()
    findings = []

    for edition in editions:
        for es in edition.edition_stories.order_by(EditionStory.rank).all():
            story = es.story
            if story is None or story.id in seen:
                continue
            seen.add(story.id)
            counts["stories_checked"] += 1
            multi = len(story.articles) >= 2
            counts["multi_article" if multi else "single_source"] += 1
            problems = []

            summary = (story.summary or "").strip()
            report = story.deep_report
            analysis_type = detect_analysis_type(story)

            if not summary:
                problems.append("no_summary")
            else:
                if qc.looks_truncated(summary):
                    problems.append("summary_truncated")
                # A single-source story's Story.summary IS the article summary
                # (edition_content.py copies it), so Smart Brevity headers and
                # their length are by design there. The story_summary prompt's
                # "one paragraph, 3-5 sentences, no labels" applies to multi only.
                if multi:
                    if re.search(r"^\s*The big picture\s*:", summary, re.I | re.M):
                        problems.append("summary_label_leak")
                    n = qc.sentence_count(summary)
                    if n and not 3 <= n <= 5:
                        problems.append(f"summary_sentences_{n}")
                    bad = qc.unsupported_numbers(summary, _source_texts(story))
                    if bad:
                        problems.append("summary_unsupported_number")

            if multi and not (report or "").strip():
                problems.append("no_deep_report")
            elif report:
                prompt = prompts.get(analysis_type) or prompts.get("default")
                missing = qc.missing_report_labels(report, prompt.current_text) if prompt else []
                if missing:
                    problems.append("report_missing_labels")
                if qc.renders_empty_the_story(report):
                    problems.append("the_story_slot_renders_empty")
                if qc.looks_truncated(report):
                    problems.append("report_truncated")
            else:
                missing = []

            for p in problems:
                counts[p] += 1
            if problems:
                findings.append({
                    "story_id": story.id,
                    "edition_id": edition.id,
                    "rank": es.rank,
                    "multi_article": multi,
                    "analysis_type": analysis_type,
                    "problems": problems,
                    "missing_labels": missing,
                    "headline": story.display_headline,
                    "summary_tail": summary[-80:] if "summary_truncated" in problems else None,
                })

    return {
        "editions": [e.id for e in editions],
        "counts": dict(counts),
        "findings": _cap(findings),
        "note": (
            "Labels are checked against the live PromptTemplate.current_text for the "
            "analysis type detect_analysis_type() picks NOW; a story whose topics changed "
            "since generation can be checked against a different variant than wrote it."
        ),
    }


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def collect_grouping(since):
    from aggregator import db
    from aggregator.models import Article, Story
    from news_fetcher.story_grouper import entity_veto, titles_are_near_duplicates

    week = datetime.utcnow() - timedelta(days=7)
    methods = dict(
        db.session.query(Article.grouping_match_method, db.func.count(Article.id))
        .filter(Article.fetched_at >= since)
        .group_by(Article.grouping_match_method)
        .all()
    )
    confidence = {}
    for method, avg, lo in (
        db.session.query(
            Article.grouping_match_method,
            db.func.avg(Article.grouping_confidence),
            db.func.min(Article.grouping_confidence),
        )
        .filter(Article.fetched_at >= since, Article.grouping_confidence.isnot(None))
        .group_by(Article.grouping_match_method)
        .all()
    ):
        confidence[str(method)] = {"avg": round(float(avg), 3), "min": round(float(lo), 3)}

    per_story = (
        db.session.query(Article.story_id.label("sid"), db.func.count(Article.id).label("n"))
        .filter(Article.story_id.isnot(None))
        .group_by(Article.story_id)
        .subquery()
    )
    ratio_rows = (
        db.session.query(per_story.c.n >= 2, db.func.count(Story.id))
        .join(Story, Story.id == per_story.c.sid)
        .filter(Story.created_at >= week)
        .group_by(per_story.c.n >= 2)
        .all()
    )
    ratio = {"multi_article": 0, "singleton": 0}
    for is_multi, n in ratio_rows:
        ratio["multi_article" if is_multi else "singleton"] = n

    pending_review = Article.query.filter(
        Article.grouping_needs_review == True,  # noqa: E712
        Article.grouping_reviewed_at.is_(None),
    ).count()

    # Singletons from the window checked against the week's multi-article stories.
    singles = (
        Story.query.join(per_story, per_story.c.sid == Story.id)
        .filter(per_story.c.n == 1, Story.created_at >= since)
        .all()
    )
    multis = (
        Story.query.join(per_story, per_story.c.sid == Story.id)
        .filter(per_story.c.n >= 2, Story.created_at >= week)
        .all()
    )
    near_dups = []
    deadline = time.monotonic() + NEAR_DUP_BUDGET_SECONDS
    truncated_scan = False
    for s in singles:
        if time.monotonic() > deadline:
            truncated_scan = True
            break
        for m in multis:
            if titles_are_near_duplicates(s.title, m.title):
                near_dups.append({
                    "singleton_story_id": s.id, "singleton_title": s.title,
                    "multi_story_id": m.id, "multi_title": m.title,
                })
                break

    # Singletons that had grouping candidates: how many would the entity veto
    # reject today? Vetoes are silent in production, so this is the stand-in.
    vetoed = []
    with_candidates = 0
    for s in singles:
        art = s.articles[0] if s.articles else None
        ids = getattr(art, "grouping_candidate_story_ids", None) if art else None
        if not ids:
            continue
        with_candidates += 1
        try:
            candidate_ids = [int(x) for x in json.loads(ids)]
        except (ValueError, TypeError):
            continue
        for cand in Story.query.filter(Story.id.in_(candidate_ids)).all():
            if entity_veto(art.title, cand):
                vetoed.append({
                    "article_id": art.id, "article_title": art.title,
                    "rejected_from_story_id": cand.id, "rejected_from_title": cand.title,
                })

    return {
        "match_method_counts_in_window": {str(k): v for k, v in methods.items()},
        "confidence_by_method": confidence,
        "confidence_note": (
            "grouping_confidence is three incompatible scales: title_overlap is a constant "
            "1.0, title_overlap_review is min(0.9, 0.6+0.05*shared_tokens), embedding_* is a "
            "real cosine. Only compare within a method."
        ),
        "stories_7d": ratio,
        "singleton_share_7d": round(
            ratio["singleton"] / max(1, ratio["singleton"] + ratio["multi_article"]), 3
        ),
        "articles_pending_review": pending_review,
        "possible_missed_merges": {
            "count": len(near_dups), "singletons_scanned": len(singles),
            "scan_truncated_by_budget": truncated_scan, "examples": _cap(near_dups),
        },
        "entity_veto_rerun": {
            "singletons_with_candidates": with_candidates,
            "would_be_vetoed": len(vetoed), "examples": _cap(vetoed),
            "note": (
                "Entity-veto suppressions are not logged in production. In these examples "
                "the story quoted is the one the article was rejected FROM, not the article."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Bias
# ---------------------------------------------------------------------------

def collect_bias(since):
    from aggregator import db
    from aggregator.models import Outlet
    from news_fetcher.allsides_lookup import ALLSIDES_BIAS

    outlets = Outlet.query.all()
    source_mix = Counter((o.bias_source or ("unrated" if o.bias_score is None else "unknown")) for o in outlets)
    unrated = [o for o in outlets if o.bias_score is None]
    diverging = [
        {"outlet": o.name, "allsides": o.allsides_bias_score, "bias_score": o.bias_score}
        for o in outlets
        if o.allsides_bias_score is not None and o.bias_score is not None
        and abs(o.allsides_bias_score - o.bias_score) >= 1.0
    ]
    abandoned = [o.name for o in unrated if (o.bias_retry_count or 0) >= 15]

    exact = {k.lower() for k in ALLSIDES_BIAS}
    folded = {}
    for key in ALLSIDES_BIAS:
        folded.setdefault(_fold_name(key), key)
    near_miss = []
    for o in outlets:
        if o.allsides_bias_score is not None or (o.name or "").lower() in exact:
            continue
        f = _fold_name(o.name)
        if not f:
            continue
        hit = folded.get(f)
        if hit is None:
            close = difflib.get_close_matches(f, folded.keys(), n=1, cutoff=0.92)
            hit = folded[close[0]] if close else None
        if hit:
            near_miss.append({"outlet": o.name, "looks_like": hit, "bias_source": o.bias_source})

    return {
        "outlets_total": len(outlets),
        "bias_source_mix": dict(source_mix),
        "unrated_outlets": len(unrated),
        "unrated_at_retry_ceiling": {"count": len(abandoned), "examples": _cap(abandoned)},
        "allsides_vs_bias_score_diverging": {"count": len(diverging), "examples": _cap(diverging)},
        "name_near_misses_against_allsides": {"count": len(near_miss), "examples": _cap(near_miss)},
        "allsides_reference_entries": len(ALLSIDES_BIAS),
        "note": (
            "There is exactly one bias reference: the hand-maintained ALLSIDES_BIAS dict, "
            "matched by outlet NAME only (exact or case-insensitive). Article-level bias is a "
            "copy of the outlet's rating unless someone used /admin/rate-article, so article "
            "bias quality is outlet bias quality. Rating quality itself needs human judgment; "
            "these are coverage and consistency checks."
        ),
    }


# ---------------------------------------------------------------------------
# Editions
# ---------------------------------------------------------------------------

def collect_editions(since):
    from aggregator.models import EditionStory
    from aggregator.article_signals import is_independent_source, low_value_article_reason
    from news_fetcher.fetch_and_store_articles import stories_look_duplicate_for_edition
    from news_fetcher.scheduler import build_headline_site_metrics

    out = {"site_metrics": build_headline_site_metrics()}
    editions = _editions_in_scope(since)
    if not editions:
        return out
    edition = editions[-1]
    rows = edition.edition_stories.order_by(EditionStory.rank).all()
    stories = [r.story for r in rows]

    dupes = []
    for i, a in enumerate(stories):
        for b in stories[i + 1:]:
            if stories_look_duplicate_for_edition(a, b):
                dupes.append({"story_ids": [a.id, b.id], "headlines": [a.display_headline, b.display_headline]})

    tail = rows[len(rows) * 2 // 3:]
    filler = []
    for r in tail:
        arts = r.story.articles
        reasons = Counter(low_value_article_reason(a.title, a.url) for a in arts)
        reasons.pop(None, None)
        independent = sum(1 for a in arts if is_independent_source(a))
        if reasons and sum(reasons.values()) >= max(1, len(arts) // 2):
            filler.append({"rank": r.rank, "story_id": r.story_id, "reasons": dict(reasons),
                           "headline": r.story.display_headline})
        elif independent == 0:
            filler.append({"rank": r.rank, "story_id": r.story_id, "reasons": {"no_independent_source": 1},
                           "headline": r.story.display_headline})

    drift = []
    for r in rows:
        if r.headline_score_at_publish is not None and r.story.headline_score is not None:
            delta = r.story.headline_score - r.headline_score_at_publish
            if abs(delta) >= 1.0:
                drift.append({"rank": r.rank, "story_id": r.story_id, "delta": round(delta, 2)})

    out.update({
        "edition_id": edition.id,
        "story_count": len(rows),
        "duplicate_pairs": {"count": len(dupes), "examples": _cap(dupes)},
        "tail_filler_candidates": {"count": len(filler), "examples": _cap(filler)},
        "score_drift_ge_1": {"count": len(drift), "examples": _cap(drift)},
    })
    return out


# ---------------------------------------------------------------------------
# Beyond the named axes
# ---------------------------------------------------------------------------

def collect_extras(since):
    from aggregator import db
    from aggregator.models import Article, PromptTemplate, Topic, article_topics
    from news_fetcher.scraper import detect_bad_scrape

    customised = [
        {"key": p.key, "current_len": len(p.current_text), "default_len": len(p.default_text)}
        for p in PromptTemplate.query.all()
        if p.current_text != p.default_text
    ]

    topic_counts = dict(
        db.session.query(Topic.name, db.func.count(article_topics.c.article_id))
        .join(article_topics, article_topics.c.topic_id == Topic.id)
        .join(Article, Article.id == article_topics.c.article_id)
        .filter(Article.fetched_at >= since)
        .group_by(Topic.name)
        .all()
    )
    total_tagged = sum(topic_counts.values())
    other = topic_counts.get("Other", 0)

    suspicious = []
    checked = 0
    for art in (
        Article.query.filter(Article.fetched_at >= since, Article.scrape_status == "success")
        .filter(Article.content.isnot(None))
        .limit(600)
        .all()
    ):
        checked += 1
        bad, reason = detect_bad_scrape(art.content)
        if bad:
            suspicious.append({"article_id": art.id, "title": art.title, "reason": reason})

    return {
        "customised_prompts": customised,
        "customised_prompts_note": "Prompt edits go live within 60s with no deploy; record these when attributing a quality change.",
        "topic_counts_in_window": topic_counts,
        "other_topic_share": round(other / total_tagged, 3) if total_tagged else None,
        "scrape_body_sanity": {"checked": checked, "flagged": len(suspicious), "examples": _cap(suspicious)},
    }


# ---------------------------------------------------------------------------
# Langfuse
# ---------------------------------------------------------------------------

def _langfuse_client():
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
            and os.environ.get("LANGFUSE_HOST")):
        return None
    from langfuse import Langfuse
    client = Langfuse()
    if not client.auth_check():
        return None
    return client


def _trace_view(t):
    return {
        "trace_id": t.id,
        "timestamp": t.timestamp.isoformat() if t.timestamp else None,
        "input": t.input, "output": t.output,
        "metadata": t.metadata, "tags": t.tags,
    }


def _norm(text):
    return " ".join(str(text or "").split())[:80]


def collect_langfuse(since, headlines, summaries, samples):
    """Attach the prompt/output pair to flagged items.

    Joins by the story:<id> tag where present (traces written after the row
    identity change); otherwise by output text, searching only a +/-3h window
    around the row's own generated_at -- the run window is wrong for an edition
    story that was summarised on an earlier run.
    """
    from datetime import timedelta, timezone
    from aggregator.models import Story

    client = _langfuse_client()
    if client is None:
        return {"status": "unavailable", "reason": "Langfuse not configured or auth_check failed"}

    def find(name, story_id, text, around):
        try:
            tagged = client.fetch_traces(name=name, tags=[f"story:{story_id}"], limit=1).data
            if tagged:
                return tagged[0], "tag"
        except Exception:
            pass
        if not text or around is None:
            return None, None
        around = around.replace(tzinfo=timezone.utc) if around.tzinfo is None else around
        want = _norm(text)
        for pg in range(1, 6):
            try:
                page = client.fetch_traces(
                    name=name, from_timestamp=around - timedelta(hours=3),
                    to_timestamp=around + timedelta(hours=3), limit=100, page=pg).data
            except Exception:
                return None, None
            for t in page:
                if t.output and _norm(t.output) == want:
                    return t, "output_match"
            if len(page) < 100:
                break
        return None, None

    attached = []

    def attach(kind, story_id, name, text, around):
        trace, how = find(name, story_id, text, around)
        attached.append({"kind": kind, "story_id": story_id, "joined_by": how,
                         "trace": _trace_view(trace) if trace else None})

    if isinstance(headlines, dict) and "findings" in headlines:
        for f in headlines["findings"][:samples]:
            story = Story.query.get(f["story_id"])
            if story is None:
                continue
            attach("headline", story.id, "generate_story_headline", story.headline,
                   getattr(story, "headline_generated_at", None))
    if isinstance(summaries, dict) and "findings" in summaries:
        for f in summaries["findings"][:samples]:
            story = Story.query.get(f["story_id"])
            if story is None:
                continue
            around = story.summary_generated_at
            if f.get("multi_article", True):
                attach("summary", story.id, "summarize_story", story.summary, around)
            elif story.articles:
                attach("summary", story.id, "summarize_article", story.summary, around)
            if story.deep_report:
                attach("deep_report", story.id, "generate_deep_report", story.deep_report, around)
    return {"status": "ok", "attached": attached}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def collect(since_arg=None, samples=5, langfuse=True):
    scope, since = resolve_scope(since_arg)
    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "scope": scope,
        "completeness": _safe("completeness", collect_completeness, since),
        "headlines": _safe("headlines", collect_headlines, since, samples),
        "summaries": _safe("summaries", collect_summaries, since, samples),
        "grouping": _safe("grouping", collect_grouping, since),
        "bias": _safe("bias", collect_bias, since),
        "editions": _safe("editions", collect_editions, since),
        "extras": _safe("extras", collect_extras, since),
    }
    if langfuse:
        report["langfuse"] = _safe(
            "langfuse", collect_langfuse, since, report["headlines"], report["summaries"], samples
        )
    else:
        report["langfuse"] = {"status": "skipped"}
    return report


def _print_human(report):
    def dump(title, value):
        print(f"\n== {title} ==")
        print(json.dumps(value, indent=2, default=str))

    print(f"Quality report generated {report['generated_at']}")
    for key in ("scope", "completeness", "headlines", "summaries", "grouping", "bias",
                "editions", "extras", "langfuse"):
        dump(key, report[key])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--since", help="ISO timestamp (UTC); default = start of the last run")
    parser.add_argument("--samples", type=int, default=5, help="Langfuse traces to attach per kind")
    parser.add_argument("--no-langfuse", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    from aggregator.app import create_app

    app = create_app()
    with app.app_context():
        report = collect(args.since, samples=args.samples, langfuse=not args.no_langfuse)
        # Belt and braces: this tool is read-only by contract.
        from aggregator import db
        db.session.rollback()

    if args.json:
        print(json.dumps(report, default=str))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
