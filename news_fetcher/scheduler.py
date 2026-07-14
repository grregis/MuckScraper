# muckscraperHeadlinesGoogleNEW/news_fetcher/scheduler.py
# news_fetcher/scheduler.py

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from aggregator import create_app, db
from aggregator.article_signals import bias_bucket_for_score
from aggregator.models import AppSetting
from news_fetcher.fetch_and_store_articles import fetch_and_store_articles, process_current_edition, review_ambiguous_grouping_matches, sync_allsides_ratings, publish_edition, retry_unrated_outlets, clear_stale_single_article_headlines
from news_fetcher.rss_fetcher import (
    fetch_and_store_rss,
    enrich_skewed_stories_with_right_feeds,
    get_skewed_story_ids_for_right_enrichment,
    enrich_skewed_stories_with_left_feeds,
    get_skewed_story_ids_for_left_enrichment,
)
from news_fetcher import llm_client
from datetime import datetime, timedelta, timezone
import logging
import sys
import os
import requests
import json
from zoneinfo import ZoneInfo

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Fetch runs can be more frequent than full edition publishing.
FETCH_SCHEDULE_HOURS = os.environ.get("FETCH_SCHEDULE_HOURS") or "7,12,17,22"
FULL_PIPELINE_HOURS = os.environ.get("FULL_PIPELINE_HOURS") or "7,17"
TIMEZONE = "America/New_York"

SCHEDULED_FETCHES = [
    # === NATIONAL / POLITICS ===
    {
        "label":          "US Politics",
        "mode":           "query",
        "country":        None,
        "category":       None,
        "query":          "US politics congress white house senate supreme court",
        "gnews_query":    "US politics congress white house",
        "gnews_category": None,
    },
    # === BUSINESS / ECONOMY ===
    {
        "label":          "Business & Economy",
        "mode":           "top",
        "country":        "us",
        "category":       "business",
        "query":          None,
        "gnews_query":    None,
        "gnews_category": "business",
    },
    # === SCIENCE / HEALTH ===
    {
        "label":          "Science & Health",
        "mode":           "query",
        "country":        None,
        "category":       None,
        "query":          "scientific breakthroughs medical research healthcare tech",
        "gnews_query":    "science health research",
        "gnews_category": "science",
    },
    # === SPORTS ===
    {
        "label":          "Sports",
        "mode":           "top",
        "country":        "us",
        "category":       "sports",
        "query":          None,
        "gnews_query":    None,
        "gnews_category": "sports",
    },
    # === WORLD NEWS ===
    {
        "label":          "World News",
        "mode":           "query",
        "country":        None,
        "category":       None,
        "query":          "international world global news conflicts diplomacy",
        "gnews_query":    "world global news",
        "gnews_category": "world",
    },
]

app = create_app()
SCRAPE_OUTCOME_HISTORY_KEY = "scrape_outcome_history_v1"
SCRAPE_OUTCOME_HISTORY_MAX_RUNS = 40


def run_optional_headline_ranking():
    """
    Run the private ranking plugin when it exists locally.
    The open-source scheduler must not require ignored/private modules.
    """
    try:
        from news_fetcher.headline_ranker import run_headline_ranking
    except ImportError as e:
        logging.info(f"--- Headline ranking skipped ({e}) ---")
        return {
            "status": "skipped",
            "reason": str(e),
        }

    run_headline_ranking()
    return {"status": "ok"}


def run_optional_static_export():
    """
    Export optional static output when an additional exporter is available.
    This keeps the main open-source stack working even when deployment-specific
    export code is not present.
    """
    try:
        from private_site.export_static import export_static_site
    except ImportError as e:
        logging.warning(
            "--- Optional static export skipped (%s). If static publishing is "
            "enabled in this deployment, make sure the extra exporter module "
            "and output mounts are available to the scheduler container. ---",
            e,
        )
        return {
            "status": "skipped",
            "reason": str(e),
        }

    export_static_site()
    return {"status": "ok"}


def _load_json_setting(key):
    setting = AppSetting.query.filter_by(key=key).first()
    if not setting or not setting.value:
        return None
    try:
        return json.loads(setting.value)
    except Exception:
        logging.warning("Could not parse JSON AppSetting for key=%s", key)
        return None


def _save_json_setting(key, value):
    payload = json.dumps(value, sort_keys=True)
    setting = AppSetting.query.filter_by(key=key).first()
    if setting:
        setting.value = payload
    else:
        db.session.add(AppSetting(key=key, value=payload))
    db.session.commit()


def _build_scrape_outcome_history_entry(run_metrics, headline_site_metrics):
    started_at = run_metrics.get("started_at")
    finished_at = run_metrics.get("finished_at")
    duration_seconds = None
    if started_at and finished_at:
        try:
            duration_seconds = int(
                (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds()
            )
        except Exception:
            duration_seconds = None

    return {
        "recorded_at": finished_at or datetime.utcnow().isoformat(),
        "status": run_metrics.get("status"),
        "duration_seconds": duration_seconds,
        "edition": headline_site_metrics.get("edition"),
        "run_scrape_statuses": dict(run_metrics.get("totals", {}).get("scrape_statuses", {})),
        "run_skipped": dict(run_metrics.get("totals", {}).get("skipped", {})),
        "run_bias_buckets": dict(run_metrics.get("totals", {}).get("bias_buckets", {})),
        "run_bias_sources": dict(run_metrics.get("totals", {}).get("bias_sources", {})),
        "headline_scrape": dict(headline_site_metrics.get("scrape", {}).get("articles_by_status", {})),
        "headline_readable_articles": headline_site_metrics.get("scrape", {}).get("readable_articles"),
        "headline_fully_read_articles": headline_site_metrics.get("scrape", {}).get("fully_read_articles"),
        "headline_blocked_articles": headline_site_metrics.get("scrape", {}).get("blocked_articles"),
        "stored_articles": run_metrics.get("totals", {}).get("stored"),
        "input_articles": run_metrics.get("totals", {}).get("input_articles"),
    }


def append_scrape_outcome_history(run_metrics, headline_site_metrics, max_runs=SCRAPE_OUTCOME_HISTORY_MAX_RUNS):
    history = _load_json_setting(SCRAPE_OUTCOME_HISTORY_KEY)
    if not isinstance(history, list):
        history = []

    history.append(_build_scrape_outcome_history_entry(run_metrics, headline_site_metrics))
    history = history[-max_runs:]
    _save_json_setting(SCRAPE_OUTCOME_HISTORY_KEY, history)
    return history


def _merge_counts(target, source):
    for key, value in (source or {}).items():
        target[key] = target.get(key, 0) + value


def _run_targeted_rss_enrichment_pass(config, run_metrics, ollama_state, entry_ranking):
    """
    Run one direction (left or right) of targeted RSS enrichment: a first
    enrichment pass, an optional bias retry + headline reranking, then a
    second pass on the reranked skewed stories with the same follow-up steps.

    Returns (ranking_result, second_pass_ranking_result) so the caller can
    chain `second_pass_ranking_result or ranking_result` into the next pass's
    `entry_ranking`.
    """
    steps = run_metrics["steps"]
    direction = config["direction"]

    logging.info(f"--- Running targeted {config['direction_adj']} RSS enrichment ---")
    try:
        enrichment_metrics = config["enrich_func"]()
        steps[config["enrichment_step"]] = enrichment_metrics
        run_metrics["totals"]["input_articles"] += enrichment_metrics.get("input_articles", 0)
        run_metrics["totals"]["stored"] += enrichment_metrics.get("stored", 0)
        run_metrics["totals"]["new_outlets"] += enrichment_metrics.get("new_outlets", 0)
        run_metrics["totals"]["stories_touched"] += enrichment_metrics.get("stories_touched", 0)
        _merge_counts(run_metrics["totals"]["skipped"], enrichment_metrics.get("skipped"))
        _merge_counts(run_metrics["totals"]["scrape_statuses"], enrichment_metrics.get("scrape_statuses"))
        _merge_counts(run_metrics["totals"]["bias_buckets"], enrichment_metrics.get("bias_buckets"))
        _merge_counts(run_metrics["totals"]["bias_sources"], enrichment_metrics.get("bias_sources"))
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in targeted {direction} RSS enrichment: {e}")
        run_metrics["status"] = "partial_error"
        enrichment_metrics = {"status": "error", "reason": str(e), "stored": 0}
        steps[config["enrichment_step"]] = enrichment_metrics

    if enrichment_metrics.get("stored", 0) > 0:
        logging.info(f"--- Refreshing outlet bias after targeted {direction} enrichment ---")
        _check_ollama_status_for_report(ollama_state, config["bias_retry_ollama_label"])
        try:
            retry_unrated_outlets()
            steps[config["bias_retry_step"]] = {"status": "ok"}
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error retrying outlet bias after targeted {direction} enrichment: {e}")
            run_metrics["status"] = "partial_error"
            steps[config["bias_retry_step"]] = {"status": "error", "reason": str(e)}
        try:
            steps[config["ranking_step"]] = run_optional_headline_ranking()
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error reranking after targeted {direction} enrichment: {e}")
            run_metrics["status"] = "partial_error"
            steps[config["ranking_step"]] = {"status": "error", "reason": str(e)}

        logging.info(f"--- Running second-pass targeted {direction} RSS enrichment on reranked skewed stories ---")
        try:
            reranked_target_ids = config["get_skewed_ids_func"](max_stories=5, candidate_pool=30)
            if reranked_target_ids:
                second_pass_metrics = config["enrich_func"](
                    max_stories=5,
                    max_articles_per_story=2,
                    candidate_story_ids=reranked_target_ids,
                )
                second_pass_metrics["target_story_ids"] = reranked_target_ids
                steps[config["second_pass_enrichment_step"]] = second_pass_metrics
                run_metrics["totals"]["input_articles"] += second_pass_metrics.get("input_articles", 0)
                run_metrics["totals"]["stored"] += second_pass_metrics.get("stored", 0)
                run_metrics["totals"]["new_outlets"] += second_pass_metrics.get("new_outlets", 0)
                run_metrics["totals"]["stories_touched"] += second_pass_metrics.get("stories_touched", 0)
                _merge_counts(run_metrics["totals"]["skipped"], second_pass_metrics.get("skipped"))
                _merge_counts(run_metrics["totals"]["scrape_statuses"], second_pass_metrics.get("scrape_statuses"))
                _merge_counts(run_metrics["totals"]["bias_buckets"], second_pass_metrics.get("bias_buckets"))
                _merge_counts(run_metrics["totals"]["bias_sources"], second_pass_metrics.get("bias_sources"))

                if second_pass_metrics.get("stored", 0) > 0:
                    _check_ollama_status_for_report(ollama_state, config["second_pass_bias_retry_ollama_label"])
                    try:
                        retry_unrated_outlets()
                        steps[config["second_pass_bias_retry_step"]] = {"status": "ok"}
                    except Exception as e:
                        db.session.rollback()
                        logging.error(f"Error retrying outlet bias after second-pass targeted {direction} enrichment: {e}")
                        run_metrics["status"] = "partial_error"
                        steps[config["second_pass_bias_retry_step"]] = {"status": "error", "reason": str(e)}
                    try:
                        steps[config["second_pass_ranking_step"]] = run_optional_headline_ranking()
                    except Exception as e:
                        db.session.rollback()
                        logging.error(f"Error reranking after second-pass targeted {direction} enrichment: {e}")
                        run_metrics["status"] = "partial_error"
                        steps[config["second_pass_ranking_step"]] = {"status": "error", "reason": str(e)}
                else:
                    steps[config["second_pass_bias_retry_step"]] = {
                        "status": "skipped",
                        "reason": "no_second_pass_enrichment_articles",
                    }
                    steps[config["second_pass_ranking_step"]] = steps[config["ranking_step"]]
            else:
                steps[config["second_pass_enrichment_step"]] = {
                    "status": "skipped",
                    "reason": "no_reranked_skewed_stories",
                }
                steps[config["second_pass_bias_retry_step"]] = {
                    "status": "skipped",
                    "reason": "no_reranked_skewed_stories",
                }
                steps[config["second_pass_ranking_step"]] = steps[config["ranking_step"]]
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error in second-pass targeted {direction} RSS enrichment: {e}")
            run_metrics["status"] = "partial_error"
            steps[config["second_pass_enrichment_step"]] = {
                "status": "error",
                "reason": str(e),
            }
            steps[config["second_pass_bias_retry_step"]] = {
                "status": "skipped",
                "reason": "second_pass_error",
            }
            steps[config["second_pass_ranking_step"]] = {
                "status": "skipped",
                "reason": "second_pass_error",
            }
    else:
        steps[config["bias_retry_step"]] = {
            "status": "skipped",
            "reason": "no_enrichment_articles",
        }
        steps[config["ranking_step"]] = entry_ranking
        steps[config["second_pass_enrichment_step"]] = {
            "status": "skipped",
            "reason": "first_pass_no_enrichment_articles",
        }
        steps[config["second_pass_bias_retry_step"]] = {
            "status": "skipped",
            "reason": "first_pass_no_enrichment_articles",
        }
        steps[config["second_pass_ranking_step"]] = steps[config["ranking_step"]]

    return steps[config["ranking_step"]], steps[config["second_pass_ranking_step"]]


RIGHT_RSS_ENRICHMENT_CONFIG = {
    "direction": "right",
    "direction_adj": "right-leaning",
    "enrich_func": enrich_skewed_stories_with_right_feeds,
    "get_skewed_ids_func": get_skewed_story_ids_for_right_enrichment,
    "enrichment_step": "targeted_right_rss_enrichment",
    "bias_retry_step": "targeted_right_rss_bias_retry",
    "ranking_step": "headline_ranking",
    "second_pass_enrichment_step": "targeted_right_rss_enrichment_second_pass",
    "second_pass_bias_retry_step": "targeted_right_rss_second_pass_bias_retry",
    "second_pass_ranking_step": "headline_ranking_post_second_pass",
    "bias_retry_ollama_label": "before_targeted_enrichment_bias_retry",
    "second_pass_bias_retry_ollama_label": "before_second_pass_targeted_enrichment_bias_retry",
}

LEFT_RSS_ENRICHMENT_CONFIG = {
    "direction": "left",
    "direction_adj": "left-leaning",
    "enrich_func": enrich_skewed_stories_with_left_feeds,
    "get_skewed_ids_func": get_skewed_story_ids_for_left_enrichment,
    "enrichment_step": "targeted_left_rss_enrichment",
    "bias_retry_step": "targeted_left_rss_bias_retry",
    "ranking_step": "headline_ranking_after_left_enrichment",
    "second_pass_enrichment_step": "targeted_left_rss_enrichment_second_pass",
    "second_pass_bias_retry_step": "targeted_left_rss_second_pass_bias_retry",
    "second_pass_ranking_step": "headline_ranking_post_left_second_pass",
    "bias_retry_ollama_label": "before_targeted_left_enrichment_bias_retry",
    "second_pass_bias_retry_ollama_label": "before_second_pass_targeted_left_enrichment_bias_retry",
}


def build_headline_site_metrics():
    from aggregator.models import Edition, EditionStory

    latest_edition = Edition.query.filter_by(published=True).order_by(
        Edition.created_at.desc()
    ).first()
    if not latest_edition:
        return {
            "status": "no_published_edition",
            "recorded_at": datetime.utcnow().isoformat(),
        }

    edition_stories = latest_edition.edition_stories.order_by(EditionStory.rank).all()
    stories = [edition_story.story for edition_story in edition_stories]
    article_ids = set()
    outlet_ids = set()
    article_bias_counts = {}
    outlet_bias_counts = {}
    outlet_bias_source_counts = {"allsides": 0, "ai": 0, "unrated": 0}
    scrape_status_counts = {
        "success": 0,
        "fallback": 0,
        "blocked": 0,
        "failed": 0,
        "skipped": 0,
        "pending": 0,
    }
    stories_with_bias_mix = 0
    stories_with_unrated_articles = 0
    multi_source_story_count = 0

    for story in stories:
        story_bias_buckets = set()
        story_outlet_ids = set()

        for article in story.articles:
            article_ids.add(article.id)
            if article.outlet_id:
                story_outlet_ids.add(article.outlet_id)

            scrape_status = (article.scrape_status or "pending").lower()
            scrape_status_counts[scrape_status] = scrape_status_counts.get(scrape_status, 0) + 1

            bucket = bias_bucket_for_score(article.bias_score if article.bias_score is not None else (article.outlet.bias_score if article.outlet else None))
            article_bias_counts[bucket] = article_bias_counts.get(bucket, 0) + 1
            story_bias_buckets.add(bucket)

            if article.outlet and article.outlet.id not in outlet_ids:
                outlet_ids.add(article.outlet.id)
                outlet_bucket = bias_bucket_for_score(article.outlet.bias_score)
                outlet_bias_counts[outlet_bucket] = outlet_bias_counts.get(outlet_bucket, 0) + 1
                bias_source = article.outlet.bias_source or "unrated"
                outlet_bias_source_counts[bias_source] = outlet_bias_source_counts.get(bias_source, 0) + 1

        if len(story_outlet_ids) > 1:
            multi_source_story_count += 1
        if len({bucket for bucket in story_bias_buckets if bucket != "unrated"}) >= 2:
            stories_with_bias_mix += 1
        if "unrated" in story_bias_buckets:
            stories_with_unrated_articles += 1

    return {
        "status": "ok",
        "recorded_at": datetime.utcnow().isoformat(),
        "edition": {
            "id": latest_edition.id,
            "date": latest_edition.date.isoformat(),
            "edition_type": latest_edition.edition_type,
            "created_at": latest_edition.created_at.isoformat() if latest_edition.created_at else None,
        },
        "story_count": len(stories),
        "article_count": len(article_ids),
        "outlet_count": len(outlet_ids),
        "multi_source_story_count": multi_source_story_count,
        "stories_with_bias_mix": stories_with_bias_mix,
        "stories_with_unrated_articles": stories_with_unrated_articles,
        "bias": {
            "articles_by_bucket": article_bias_counts,
            "outlets_by_bucket": outlet_bias_counts,
            "outlets_by_source": outlet_bias_source_counts,
        },
        "scrape": {
            "articles_by_status": scrape_status_counts,
            "readable_articles": scrape_status_counts.get("success", 0) + scrape_status_counts.get("fallback", 0),
            "fully_read_articles": scrape_status_counts.get("success", 0),
            "blocked_articles": scrape_status_counts.get("blocked", 0),
        },
    }


def get_last_fetch_time():
    """Get the last fetch timestamp from the database."""
    setting = AppSetting.query.filter_by(key="last_fetch").first()
    if setting and setting.value:
        try:
            return datetime.fromisoformat(setting.value)
        except Exception:
            return None
    return None


def set_last_fetch_time():
    """Store the current time as the last fetch timestamp."""
    setting = AppSetting.query.filter_by(key="last_fetch").first()
    if setting:
        setting.value = datetime.utcnow().isoformat()
    else:
        setting = AppSetting(key="last_fetch", value=datetime.utcnow().isoformat())
        db.session.add(setting)
    db.session.commit()


def get_last_allsides_sync():
    """Get the last AllSides sync timestamp from the database."""
    setting = AppSetting.query.filter_by(key="last_allsides_sync").first()
    if setting and setting.value:
        try:
            return datetime.fromisoformat(setting.value)
        except Exception:
            return None
    return None


def set_last_allsides_sync():
    """Store the current time as the last AllSides sync timestamp."""
    setting = AppSetting.query.filter_by(key="last_allsides_sync").first()
    if setting:
        setting.value = datetime.utcnow().isoformat()
    else:
        setting = AppSetting(key="last_allsides_sync", value=datetime.utcnow().isoformat())
        db.session.add(setting)
    db.session.commit()


def _parse_schedule_hours(raw_hours):
    return sorted(int(hour.strip()) for hour in raw_hours.split(",") if hour.strip())


def _scheduled_hours():
    return _parse_schedule_hours(FETCH_SCHEDULE_HOURS)


def _full_pipeline_hours():
    return _parse_schedule_hours(FULL_PIPELINE_HOURS)


def _latest_scheduled_run_before(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    local_tz = ZoneInfo(TIMEZONE)
    local_now = now.astimezone(local_tz)

    candidates = []
    for day_offset in (0, -1):
        candidate_date = (local_now + timedelta(days=day_offset)).date()
        for hour in _scheduled_hours():
            candidates.append(
                datetime(
                    candidate_date.year,
                    candidate_date.month,
                    candidate_date.day,
                    hour,
                    0,
                    tzinfo=local_tz,
                )
            )

    eligible = [candidate for candidate in candidates if candidate <= local_now]
    if not eligible:
        return None
    return max(eligible).astimezone(timezone.utc)


def _latest_full_pipeline_run_before(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    local_tz = ZoneInfo(TIMEZONE)
    local_now = now.astimezone(local_tz)

    candidates = []
    for day_offset in (0, -1):
        candidate_date = (local_now + timedelta(days=day_offset)).date()
        for hour in _full_pipeline_hours():
            candidates.append(
                datetime(
                    candidate_date.year,
                    candidate_date.month,
                    candidate_date.day,
                    hour,
                    0,
                    tzinfo=local_tz,
                )
            )

    eligible = [candidate for candidate in candidates if candidate <= local_now]
    if not eligible:
        return None
    return max(eligible).astimezone(timezone.utc)


def should_fetch_now(now=None, last_fetch=None):
    """
    Returns True on startup only when the app missed a scheduled fetch slot.
    This avoids ad-hoc catch-up runs based on elapsed time alone.
    """
    if last_fetch is None:
        last_fetch = get_last_fetch_time()

    if not last_fetch:
        logging.info("No record of previous fetch. Initializing with a startup fetch.")
        return True

    if last_fetch.tzinfo is None:
        last_fetch = last_fetch.replace(tzinfo=timezone.utc)

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    latest_scheduled_run = _latest_scheduled_run_before(now=now)
    if latest_scheduled_run and last_fetch < latest_scheduled_run:
        logging.info(
            "Last fetch at %s missed scheduled run at %s, fetching on startup.",
            last_fetch.isoformat(),
            latest_scheduled_run.isoformat(),
        )
        return True

    logging.info(
        "Last fetch at %s is up to date with scheduled runs. Skipping startup fetch.",
        last_fetch.isoformat(),
    )
    return False


def should_run_full_pipeline(now=None, last_fetch=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if last_fetch is not None:
        if last_fetch.tzinfo is None:
            last_fetch = last_fetch.replace(tzinfo=timezone.utc)
        latest_full_slot = _latest_full_pipeline_run_before(now=now)
        return bool(latest_full_slot and last_fetch < latest_full_slot)

    local_now = now.astimezone(ZoneInfo(TIMEZONE))
    return local_now.hour in _full_pipeline_hours()


def _notify_n8n():
    webhook = os.getenv("N8N_WEBHOOK_URL")
    if not webhook:
        return
    try:
        from news_fetcher.summarizer import check_ollama_status

        if not check_ollama_status():
            logging.info("  [n8n] Ollama already unreachable, skipping suspend webhook")
            return

        response = requests.post(webhook, timeout=5)
        response.raise_for_status()
        logging.info(
            "  [n8n] Webhook fired — Ollama suspend sequence triggered (status %s)",
            response.status_code,
        )
    except Exception as e:
        logging.warning(f"  [n8n] Webhook failed ({e}) — continuing normally")


def _check_ollama_status_for_report(ollama_state, label):
    from news_fetcher.summarizer import check_ollama_status

    try:
        is_up = check_ollama_status()
    except Exception as e:
        logging.warning("  [Ollama] Health check failed during %s (%s)", label, e)
        is_up = False

    checked_at = datetime.utcnow().isoformat()
    ollama_state["checks"].append({
        "at": checked_at,
        "label": label,
        "up": is_up,
    })
    if not is_up:
        ollama_state["went_down_during_run"] = True
    return is_up


def _build_fetch_report(run_metrics, headline_site_metrics, ollama_state):
    started_at = datetime.fromisoformat(run_metrics["started_at"])
    finished_at = datetime.fromisoformat(run_metrics["finished_at"])
    duration_seconds = int((finished_at - started_at).total_seconds())
    run_totals = run_metrics.get("totals", {})
    headline_scrape = dict(headline_site_metrics.get("scrape", {}).get("articles_by_status", {}))
    edition = headline_site_metrics.get("edition", {}) or {}
    headline_metrics_label = "Latest published edition stats"

    return {
        "status": run_metrics.get("status", "unknown"),
        "started_at": run_metrics["started_at"],
        "finished_at": run_metrics["finished_at"],
        "duration_seconds": duration_seconds,
        # Backward-compatible top-level summary fields for n8n formatters.
        "input_articles": run_totals.get("input_articles", 0),
        "stored_articles": run_totals.get("stored", 0),
        "new_outlets": run_totals.get("new_outlets", 0),
        "stories_touched": run_totals.get("stories_touched", 0),
        "run_scrape_statuses": dict(run_totals.get("scrape_statuses", {})),
        "headline_metrics_label": headline_metrics_label,
        "headline_scrape": headline_scrape,
        "headline_readable_articles": headline_site_metrics.get("scrape", {}).get("readable_articles", 0),
        "headline_fully_read_articles": headline_site_metrics.get("scrape", {}).get("fully_read_articles", 0),
        "headline_blocked_articles": headline_site_metrics.get("scrape", {}).get("blocked_articles", 0),
        "edition": {
            "id": edition.get("id"),
            "date": edition.get("date"),
            "edition_type": edition.get("edition_type"),
        },
        "ollama": ollama_state,
        "run_metrics": run_metrics,
        "headline_metrics": headline_site_metrics,
        "latest_published_edition_metrics": headline_site_metrics,
    }


def _notify_fetch_report(report_payload):
    webhook = os.getenv("N8N_FETCH_REPORT_WEBHOOK_URL")
    if not webhook:
        return

    try:
        response = requests.post(webhook, json=report_payload, timeout=10)
        response.raise_for_status()
        logging.info(
            "  [n8n] Fetch report webhook fired (status %s)",
            response.status_code,
        )
    except Exception as e:
        logging.warning(f"  [n8n] Fetch report webhook failed ({e}) — continuing normally")


def run_all_fetches(run_full_pipeline=True):
    logging.info("=== Starting scheduled fetch run ===")
    with app.app_context():
        ollama_state = {
            "up_at_start": False,
            "up_at_end": False,
            "went_down_during_run": False,
            "checks": [],
        }
        run_metrics = {
            "status": "ok",
            "started_at": datetime.utcnow().isoformat(),
            "topics": {},
            "rss": None,
            "totals": {
                "input_articles": 0,
                "stored": 0,
                "new_outlets": 0,
                "stories_touched": 0,
                "skipped": {},
                "scrape_statuses": {},
                "bias_buckets": {},
                "bias_sources": {},
            },
            "steps": {},
        }
        ollama_state["up_at_start"] = _check_ollama_status_for_report(ollama_state, "run_start")

        # Fetch all categories
        for fetch in SCHEDULED_FETCHES:
            logging.info(f"--- Fetching: {fetch['label']} ---")
            try:
                topic_metrics = fetch_and_store_articles(
                    fetch["label"],
                    mode=fetch["mode"],
                    query=fetch["query"],
                    country=fetch["country"],
                    category=fetch["category"],
                    gnews_query=fetch["gnews_query"],
                    gnews_category=fetch["gnews_category"]
                )
                run_metrics["topics"][fetch["label"]] = topic_metrics
                for provider_metrics in topic_metrics.get("providers", {}).values():
                    run_metrics["totals"]["input_articles"] += provider_metrics.get("input_articles", 0)
                    run_metrics["totals"]["stored"] += provider_metrics.get("stored", 0)
                    run_metrics["totals"]["new_outlets"] += provider_metrics.get("new_outlets", 0)
                    run_metrics["totals"]["stories_touched"] += provider_metrics.get("stories_touched", 0)
                    _merge_counts(run_metrics["totals"]["skipped"], provider_metrics.get("skipped"))
                    _merge_counts(run_metrics["totals"]["scrape_statuses"], provider_metrics.get("scrape_statuses"))
                    _merge_counts(run_metrics["totals"]["bias_buckets"], provider_metrics.get("bias_buckets"))
                    _merge_counts(run_metrics["totals"]["bias_sources"], provider_metrics.get("bias_sources"))
            except Exception as e:
                db.session.rollback()
                logging.error(f"Error fetching {fetch['label']}: {e}")
                run_metrics["status"] = "partial_error"
                run_metrics["topics"][fetch["label"]] = {
                    "status": "error",
                    "reason": str(e),
                }

        # Run RSS fetch for major wire services and networks
        logging.info("--- Fetching RSS feeds ---")
        try:
            rss_metrics = fetch_and_store_rss()
            run_metrics["rss"] = rss_metrics
            run_metrics["totals"]["input_articles"] += rss_metrics.get("input_articles", 0)
            run_metrics["totals"]["stored"] += rss_metrics.get("stored", 0)
            run_metrics["totals"]["new_outlets"] += rss_metrics.get("new_outlets", 0)
            run_metrics["totals"]["stories_touched"] += rss_metrics.get("stories_touched", 0)
            _merge_counts(run_metrics["totals"]["skipped"], rss_metrics.get("skipped"))
            _merge_counts(run_metrics["totals"]["scrape_statuses"], rss_metrics.get("scrape_statuses"))
            _merge_counts(run_metrics["totals"]["bias_buckets"], rss_metrics.get("bias_buckets"))
            _merge_counts(run_metrics["totals"]["bias_sources"], rss_metrics.get("bias_sources"))
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error fetching RSS feeds: {e}")
            run_metrics["status"] = "partial_error"
            run_metrics["rss"] = {
                "status": "error",
                "reason": str(e),
            }

        if run_full_pipeline:
            # Run Bias Checker ONCE after all fetches
            logging.info("--- Retrying unrated outlets (Bias Checker) ---")
            _check_ollama_status_for_report(ollama_state, "before_retry_unrated_outlets")
            try:
                retry_unrated_outlets()
                run_metrics["steps"]["retry_unrated_outlets"] = {"status": "ok"}
            except Exception as e:
                db.session.rollback()
                logging.error(f"Error checking outlet bias: {e}")
                run_metrics["status"] = "partial_error"
                run_metrics["steps"]["retry_unrated_outlets"] = {"status": "error", "reason": str(e)}

            # Run AllSides sync once a month
            last_sync = get_last_allsides_sync()
            if last_sync is None or (datetime.utcnow() - last_sync).days >= 30:
                logging.info("--- Syncing AllSides bias ratings ---")
                try:
                    sync_allsides_ratings()
                    set_last_allsides_sync()
                    run_metrics["steps"]["allsides_sync"] = {"status": "ok"}
                except Exception as e:
                    db.session.rollback()
                    logging.error(f"Error syncing AllSides ratings: {e}")
                    run_metrics["status"] = "partial_error"
                    run_metrics["steps"]["allsides_sync"] = {"status": "error", "reason": str(e)}
            else:
                days_since = (datetime.utcnow() - last_sync).days
                logging.info(f"--- AllSides sync skipped ({days_since}/30 days) ---")
                run_metrics["steps"]["allsides_sync"] = {
                    "status": "skipped",
                    "days_since_last_sync": days_since,
                }

            logging.info("--- Running headline ranking ---")
            _check_ollama_status_for_report(ollama_state, "before_headline_ranking")
            try:
                cleared = clear_stale_single_article_headlines()
                run_metrics["steps"]["clear_stale_single_article_headlines"] = {
                    "status": "ok",
                    "cleared": cleared,
                }
            except Exception as e:
                db.session.rollback()
                logging.error(f"Error clearing stale single-article headlines: {e}")
                run_metrics["status"] = "partial_error"
                run_metrics["steps"]["clear_stale_single_article_headlines"] = {"status": "error", "reason": str(e)}
            try:
                run_metrics["steps"]["review_ambiguous_grouping_matches"] = review_ambiguous_grouping_matches()
            except Exception as e:
                db.session.rollback()
                logging.error(f"Error reviewing ambiguous grouping matches: {e}")
                run_metrics["status"] = "partial_error"
                run_metrics["steps"]["review_ambiguous_grouping_matches"] = {"status": "error", "reason": str(e)}
            try:
                initial_ranking = run_optional_headline_ranking()
                run_metrics["steps"]["headline_ranking_initial"] = initial_ranking
            except Exception as e:
                db.session.rollback()
                logging.error(f"Error in headline ranking: {e}")
                run_metrics["status"] = "partial_error"
                initial_ranking = {"status": "error", "reason": str(e)}
                run_metrics["steps"]["headline_ranking_initial"] = initial_ranking

            if llm_client.LLM_PROVIDER == "groq":
                # Each pass re-runs run_optional_headline_ranking() (batched Groq calls
                # plus an editorial_rerank() call), so the 4 extra passes below cost up
                # to 4x the Groq load of the single initial ranking above. Skip them
                # while on Groq's free tier; re-enable once Ollama is back.
                logging.info("--- Skipping targeted left/right RSS enrichment passes (LLM_PROVIDER=groq) ---")
                for step in (
                    "targeted_right_rss_enrichment", "targeted_right_rss_bias_retry", "headline_ranking",
                    "targeted_right_rss_enrichment_second_pass", "targeted_right_rss_second_pass_bias_retry",
                    "headline_ranking_post_second_pass", "targeted_left_rss_enrichment", "targeted_left_rss_bias_retry",
                    "headline_ranking_after_left_enrichment", "targeted_left_rss_enrichment_second_pass",
                    "targeted_left_rss_second_pass_bias_retry", "headline_ranking_post_left_second_pass",
                ):
                    run_metrics["steps"][step] = {"status": "skipped", "reason": "groq_provider"}
            else:
                right_ranking, right_second_pass_ranking = _run_targeted_rss_enrichment_pass(
                    RIGHT_RSS_ENRICHMENT_CONFIG, run_metrics, ollama_state, initial_ranking
                )

                _run_targeted_rss_enrichment_pass(
                    LEFT_RSS_ENRICHMENT_CONFIG, run_metrics, ollama_state,
                    right_second_pass_ranking or right_ranking,
                )

            logging.info("--- Publishing edition ---")
            try:
                run_metrics["steps"]["publish_edition"] = publish_edition()
            except Exception as e:
                db.session.rollback()
                logging.error(f"Error publishing edition: {e}")
                run_metrics["status"] = "partial_error"
                run_metrics["steps"]["publish_edition"] = {"status": "error", "reason": str(e)}

            logging.info("--- Processing current edition content ---")
            _check_ollama_status_for_report(ollama_state, "before_process_current_edition")
            try:
                run_metrics["steps"]["process_current_edition"] = process_current_edition()
            except Exception as e:
                db.session.rollback()
                logging.error(f"Error processing edition content: {e}")
                run_metrics["status"] = "partial_error"
                run_metrics["steps"]["process_current_edition"] = {"status": "error", "reason": str(e)}

            logging.info("--- Exporting static site ---")
            try:
                run_metrics["steps"]["static_export"] = run_optional_static_export()
            except Exception as e:
                db.session.rollback()
                logging.error(f"Error exporting static site: {e}")
                run_metrics["status"] = "partial_error"
                run_metrics["steps"]["static_export"] = {"status": "error", "reason": str(e)}
        else:
            run_metrics["steps"]["retry_unrated_outlets"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["allsides_sync"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["clear_stale_single_article_headlines"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["review_ambiguous_grouping_matches"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["headline_ranking_initial"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["targeted_right_rss_enrichment"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["targeted_right_rss_bias_retry"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["headline_ranking"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["targeted_right_rss_enrichment_second_pass"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["targeted_right_rss_second_pass_bias_retry"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["targeted_left_rss_enrichment"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["targeted_left_rss_bias_retry"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["headline_ranking_after_left_enrichment"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["targeted_left_rss_enrichment_second_pass"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["targeted_left_rss_second_pass_bias_retry"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["headline_ranking_post_left_second_pass"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["headline_ranking_post_second_pass"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["publish_edition"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["process_current_edition"] = {"status": "skipped", "reason": "fetch_only_run"}
            run_metrics["steps"]["static_export"] = {"status": "skipped", "reason": "fetch_only_run"}

        set_last_fetch_time()
        run_metrics["finished_at"] = datetime.utcnow().isoformat()
        ollama_state["up_at_end"] = _check_ollama_status_for_report(ollama_state, "run_end")
        _save_json_setting("last_run_metrics", run_metrics)
        headline_site_metrics = build_headline_site_metrics()
        _save_json_setting("last_headline_site_metrics", headline_site_metrics)
        append_scrape_outcome_history(run_metrics, headline_site_metrics)
        fetch_report = _build_fetch_report(run_metrics, headline_site_metrics, ollama_state)
        _save_json_setting("last_fetch_report", fetch_report)
        logging.info(
            "[Metrics] Run stored=%s input=%s latest_edition=%s %s",
            run_metrics["totals"]["stored"],
            run_metrics["totals"]["input_articles"],
            headline_site_metrics.get("edition", {}).get("date"),
            headline_site_metrics.get("edition", {}).get("edition_type"),
        )
        _notify_fetch_report(fetch_report)

        # Notify n8n pipeline is done — triggers Ollama machine suspend
        _notify_n8n()

    logging.info("=== Scheduled fetch run complete ===")


if __name__ == "__main__":
    logging.info("Scheduler starting up...")

    with app.app_context():
        db.create_all()
        # Only fetch on startup if enough time has passed
        if should_fetch_now():
            run_all_fetches(
                run_full_pipeline=should_run_full_pipeline(
                    last_fetch=get_last_fetch_time(),
                )
            )
        else:
            logging.info("Skipping startup fetch.")

    scheduler = BlockingScheduler()
    scheduler.add_job(
        lambda: run_all_fetches(run_full_pipeline=should_run_full_pipeline()),
        trigger=CronTrigger(hour=FETCH_SCHEDULE_HOURS, minute=0, timezone=TIMEZONE),
        id="fetch_job",
        name="Scheduled news fetch (America/New_York)",
        replace_existing=True
    )

    logging.info(
        "Scheduler running. Fetching at %s and running full pipeline at %s in %s.",
        FETCH_SCHEDULE_HOURS,
        FULL_PIPELINE_HOURS,
        TIMEZONE,
    )
    scheduler.start()
