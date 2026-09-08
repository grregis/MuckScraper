import logging
from datetime import datetime
from flask import render_template, request, redirect, url_for
from flask_login import login_required
from sqlalchemy import func
from aggregator import db
from aggregator.models import AppSetting, Topic, RssFeed, PromptTemplate, PipelineSchedule, ScheduledFetch, IngestionBlock
from news_fetcher.prompt_registry import validate_prompt_text, invalidate_cache as invalidate_prompt_cache, KNOWN_VARS as PROMPT_KNOWN_VARS

from . import admin

logger = logging.getLogger(__name__)


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


@admin.route("/prompts")
@login_required
def prompts_page():
    prompts = PromptTemplate.query.order_by(PromptTemplate.key.asc()).all()
    return render_template("prompts.html", prompts=prompts)


@admin.route("/prompts/<key>/edit")
@login_required
def edit_prompt(key):
    prompt = PromptTemplate.query.filter_by(key=key).first_or_404()
    saved = request.args.get("saved") == "1"
    return render_template(
        "prompt_edit.html", prompt=prompt, submitted_text=prompt.current_text,
        error=None, warning=None, saved=saved, known_vars=sorted(PROMPT_KNOWN_VARS.get(key, set())),
    )


@admin.route("/prompts/<key>/update", methods=["POST"])
@login_required
def update_prompt(key):
    prompt = PromptTemplate.query.filter_by(key=key).first_or_404()
    text = request.form.get("current_text", "")
    known_vars = sorted(PROMPT_KNOWN_VARS.get(key, set()))

    error, warning = validate_prompt_text(key, text)
    if error:
        return render_template(
            "prompt_edit.html", prompt=prompt, submitted_text=text,
            error=error, warning=None, saved=False, known_vars=known_vars,
        )

    prompt.current_text = text
    prompt.updated_at = datetime.utcnow()
    db.session.commit()
    invalidate_prompt_cache(key)

    if warning:
        return render_template(
            "prompt_edit.html", prompt=prompt, submitted_text=prompt.current_text,
            error=None, warning=warning, saved=True, known_vars=known_vars,
        )
    return redirect(url_for("admin.edit_prompt", key=key, saved=1))


@admin.route("/prompts/<key>/reset", methods=["POST"])
@login_required
def reset_prompt(key):
    prompt = PromptTemplate.query.filter_by(key=key).first_or_404()
    prompt.current_text = prompt.default_text
    prompt.updated_at = None
    db.session.commit()
    invalidate_prompt_cache(key)
    return redirect(url_for("admin.edit_prompt", key=key, saved=1))


def _mark_pipeline_schedule_changed():
    """Record that PipelineSchedule was edited -- compared against
    scheduler_started_at (set by scheduler.py on boot) to tell whether the
    scheduler still needs a restart to pick up the change."""
    now = datetime.utcnow().isoformat()
    setting = AppSetting.query.filter_by(key="pipeline_schedule_changed_at").first()
    if setting:
        setting.value = now
    else:
        db.session.add(AppSetting(key="pipeline_schedule_changed_at", value=now))
    db.session.commit()


def pipeline_schedule_restart_needed():
    changed = AppSetting.query.filter_by(key="pipeline_schedule_changed_at").first()
    if not changed or not changed.value:
        return False
    started = AppSetting.query.filter_by(key="scheduler_started_at").first()
    if not started or not started.value:
        # Schedule has been edited but the scheduler has never recorded a
        # start (e.g. this migration/feature is newer than its last boot).
        return True
    return changed.value > started.value


@admin.route("/pipeline-schedule")
@login_required
def pipeline_schedule_page():
    entries = PipelineSchedule.query.order_by(PipelineSchedule.hour.asc()).all()
    return render_template(
        "pipeline_schedule.html", entries=entries, restart_needed=pipeline_schedule_restart_needed(),
    )


@admin.route("/pipeline-schedule/add", methods=["POST"])
@login_required
def add_pipeline_schedule():
    hour = request.form.get("hour", type=int)
    run_full_pipeline = request.form.get("run_full_pipeline") == "on"

    if hour is not None and 0 <= hour <= 23 and not PipelineSchedule.query.filter_by(hour=hour).first():
        db.session.add(PipelineSchedule(
            hour=hour,
            run_full_pipeline=run_full_pipeline,
            is_active=True,
        ))
        db.session.commit()
        _mark_pipeline_schedule_changed()
        logger.info(f"[PipelineSchedule] Added scheduled run at {hour}:00 (full_pipeline={run_full_pipeline})")
    return redirect(url_for("admin.pipeline_schedule_page"))


@admin.route("/pipeline-schedule/<int:entry_id>/update", methods=["POST"])
@login_required
def update_pipeline_schedule(entry_id):
    entry = PipelineSchedule.query.get_or_404(entry_id)
    hour = request.form.get("hour", type=int)

    if hour is not None and 0 <= hour <= 23:
        entry.hour = hour
    entry.run_full_pipeline = request.form.get("run_full_pipeline") == "on"
    entry.is_active = request.form.get("is_active") == "on"
    db.session.commit()
    _mark_pipeline_schedule_changed()
    return redirect(url_for("admin.pipeline_schedule_page"))


@admin.route("/pipeline-schedule/<int:entry_id>/delete", methods=["POST"])
@login_required
def delete_pipeline_schedule(entry_id):
    entry = PipelineSchedule.query.get_or_404(entry_id)
    db.session.delete(entry)
    db.session.commit()
    _mark_pipeline_schedule_changed()
    return redirect(url_for("admin.pipeline_schedule_page"))


INGESTION_BLOCK_KINDS = ["source", "title_keyword"]


@admin.route("/ingestion-blocks")
@login_required
def ingestion_blocks_page():
    blocks_by_kind = {
        kind: IngestionBlock.query.filter_by(kind=kind).order_by(
            IngestionBlock.note.asc(), IngestionBlock.pattern.asc()
        ).all()
        for kind in INGESTION_BLOCK_KINDS
    }
    return render_template(
        "ingestion_blocks.html", blocks_by_kind=blocks_by_kind, kinds=INGESTION_BLOCK_KINDS,
    )


@admin.route("/ingestion-blocks/add", methods=["POST"])
@login_required
def add_ingestion_block():
    kind = request.form.get("kind", "").strip()
    # Stored lowercase because that is how both checks compare -- normalizing
    # on the way in keeps the unique constraint honest, so "NFL.com" can't be
    # added alongside an existing "nfl.com" as a second row that never fires.
    pattern = request.form.get("pattern", "").strip().lower()
    note = request.form.get("note", "").strip() or None

    if pattern and kind in INGESTION_BLOCK_KINDS and not IngestionBlock.query.filter_by(kind=kind, pattern=pattern).first():
        db.session.add(IngestionBlock(kind=kind, pattern=pattern, note=note, is_active=True))
        db.session.commit()
        logger.info(f"[IngestionBlocks] Added {kind} block: {pattern}")
    return redirect(url_for("admin.ingestion_blocks_page"))


@admin.route("/ingestion-blocks/<int:block_id>/update", methods=["POST"])
@login_required
def update_ingestion_block(block_id):
    block = IngestionBlock.query.get_or_404(block_id)
    pattern = request.form.get("pattern", "").strip().lower()

    clash = IngestionBlock.query.filter(
        IngestionBlock.kind == block.kind,
        IngestionBlock.pattern == pattern,
        IngestionBlock.id != block.id,
    ).first()
    if pattern and not clash:
        block.pattern = pattern
    block.note = request.form.get("note", "").strip() or None
    block.is_active = request.form.get("is_active") == "on"
    db.session.commit()
    return redirect(url_for("admin.ingestion_blocks_page"))


@admin.route("/ingestion-blocks/<int:block_id>/delete", methods=["POST"])
@login_required
def delete_ingestion_block(block_id):
    block = IngestionBlock.query.get_or_404(block_id)
    db.session.delete(block)
    db.session.commit()
    return redirect(url_for("admin.ingestion_blocks_page"))


SCHEDULED_FETCH_MODES = ["query", "top"]


def _scheduled_fetch_form_values(form):
    """
    Pull a ScheduledFetch's editable fields off a submitted form.

    The unused half of the mode pair is blanked rather than kept: leaving a
    stale `query` on a row switched to "top" mode would be invisible in the
    UI but still sitting in the DB, ready to confuse the next person who
    switches it back.
    """
    mode = (form.get("mode", "") or "").strip()
    if mode not in SCHEDULED_FETCH_MODES:
        mode = "query"

    def field(name):
        return (form.get(name, "") or "").strip() or None

    values = {
        "mode":           mode,
        "description":    field("description"),
        "gnews_query":    field("gnews_query"),
        "gnews_category": field("gnews_category"),
        "newsapi_country":  field("newsapi_country") if mode == "top" else None,
        "newsapi_category": field("newsapi_category") if mode == "top" else None,
        "newsapi_query":    field("newsapi_query") if mode == "query" else None,
    }
    return values


@admin.route("/scheduled-fetches")
@login_required
def scheduled_fetches_page():
    fetches = ScheduledFetch.query.order_by(
        ScheduledFetch.sort_order.asc(), ScheduledFetch.id.asc()
    ).all()
    return render_template(
        "scheduled_fetches.html", fetches=fetches, modes=SCHEDULED_FETCH_MODES,
    )


@admin.route("/scheduled-fetches/add", methods=["POST"])
@login_required
def add_scheduled_fetch():
    label = request.form.get("label", "").strip()

    if label and not ScheduledFetch.query.filter_by(label=label).first():
        values = _scheduled_fetch_form_values(request.form)
        next_order = (db.session.query(func.max(ScheduledFetch.sort_order)).scalar() or 0) + 1
        db.session.add(ScheduledFetch(
            label=label, sort_order=next_order, is_active=True, **values
        ))
        db.session.commit()
        logger.info(f"[ScheduledFetches] Added fetch: {label} (mode={values['mode']})")
    return redirect(url_for("admin.scheduled_fetches_page"))


@admin.route("/scheduled-fetches/<int:fetch_id>/update", methods=["POST"])
@login_required
def update_scheduled_fetch(fetch_id):
    fetch = ScheduledFetch.query.get_or_404(fetch_id)
    label = request.form.get("label", "").strip()
    sort_order = request.form.get("sort_order", type=int)

    # Labels key per-run metrics, so a collision would silently merge two
    # fetches' results -- reject the edit and keep the existing label.
    clash = ScheduledFetch.query.filter(
        ScheduledFetch.label == label, ScheduledFetch.id != fetch.id
    ).first()
    if label and not clash:
        fetch.label = label

    for key, value in _scheduled_fetch_form_values(request.form).items():
        setattr(fetch, key, value)
    if sort_order is not None:
        fetch.sort_order = sort_order
    fetch.is_active = request.form.get("is_active") == "on"
    db.session.commit()
    return redirect(url_for("admin.scheduled_fetches_page"))


@admin.route("/scheduled-fetches/<int:fetch_id>/delete", methods=["POST"])
@login_required
def delete_scheduled_fetch(fetch_id):
    fetch = ScheduledFetch.query.get_or_404(fetch_id)
    db.session.delete(fetch)
    db.session.commit()
    return redirect(url_for("admin.scheduled_fetches_page"))
