"""Search page: dense story and article tables with no images.

Two tabs share one query box and one time window:
- Stories: a story matches when any of its articles is in the window (the same
  rule as the story listings). Counts cover all of the story's articles.
- Articles: an article matches on its own publish date.

Text queries go through Meilisearch, capped at SEARCH_CANDIDATE_LIMIT hits, and
the exact window is then applied in SQL. Without a query the tables are browsed
straight from SQL.
"""
import logging
from datetime import datetime, timedelta

from flask import render_template, request, url_for
from flask_login import login_required
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import aliased

from aggregator import db
from aggregator.display_time import local_day_end_utc, local_day_start_utc
from aggregator.models import Article, Outlet, Story, Topic, story_topics
from aggregator.search import SearchUnavailableError, search_article_ids, search_story_ids

from . import admin
from .articles import DEFAULT_TIME_RANGE, TIME_RANGES

logger = logging.getLogger(__name__)

SEARCH_TABS = ("stories", "articles")
SEARCH_PER_PAGE = 50
SEARCH_CANDIDATE_LIMIT = 1000
# Bias buckets: the same thresholds as story_bias_totals() in admin/_shared.py,
# so the Left / Center / Right columns agree with the grouped view. Keep them in step.
LEFT_MAX = 2.5
CENTER_MAX = 3.5

# (key, label, numeric, default direction) for each tab's sortable columns.
STORY_COLUMNS = [
    ("headline", "Headline", False, "asc"),
    ("topics", "Topics", False, None),
    ("articles", "Articles", True, "desc"),
    # Distinct outlets: one outlet can file many articles on a story (The Hill
    # filed 30 of the 125 on the 09-18 White House press-ban story), so this,
    # not Articles, is "most reported" (GitHub issue #6).
    ("sources", "Sources", True, "desc"),
    ("left", "Left", True, "desc"),
    ("center", "Center", True, "desc"),
    ("right", "Right", True, "desc"),
    ("created", "Created", False, "desc"),
    ("updated", "Latest article", False, "desc"),
]
ARTICLE_COLUMNS = [
    ("title", "Title", False, "asc"),
    ("outlet", "Outlet", False, "asc"),
    ("date", "Published", False, "desc"),
    ("bias", "Bias", True, "desc"),
    ("story", "Story", False, "asc"),
    ("story_articles", "Story articles", True, "desc"),
]
STORY_SORTABLE = {"headline", "articles", "sources", "left", "center", "right", "created", "updated"}
ARTICLE_SORTABLE = {"title", "outlet", "date", "bias", "story", "story_articles"}


# With a text query the rows default to Meilisearch's own ranking, best match
# first. Sorting the <= 1000 candidates by date instead put strong matches
# pages behind weak recent ones.
RELEVANCE = "relevance"


def _relevance_order(id_column, ranked_ids):
    """ORDER BY position in Meilisearch's ranked id list, or None if there is
    no ranked list (no query, or the SQL fallback)."""
    if not ranked_ids:
        return None
    return case({row_id: index for index, row_id in enumerate(ranked_ids)}, value=id_column)


def _parse_day(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _resolve_window(args):
    """The time window as {since, until, range, start, end}. `since` is inclusive
    and `until` exclusive; either may be None (no bound). A bad custom range
    falls back to the default."""
    key = args.get("range", DEFAULT_TIME_RANGE)
    if key == "custom":
        start = _parse_day(args.get("start"))
        end = _parse_day(args.get("end"))
        if start and end and start <= end:
            # Whole days in the display time zone, as naive UTC bounds.
            return {
                "range": "custom",
                "since": local_day_start_utc(start.date()),
                "until": local_day_end_utc(end.date()),
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
            }
        key = DEFAULT_TIME_RANGE
    if key not in TIME_RANGES:
        key = DEFAULT_TIME_RANGE
    span = TIME_RANGES[key]
    since = datetime.utcnow() - span if span is not None else None
    return {"range": key, "since": since, "until": None, "start": "", "end": ""}


def _in_window(column, since, until):
    clauses = []
    if since is not None:
        clauses.append(column >= since)
    if until is not None:
        clauses.append(column < until)
    return and_(*clauses) if clauses else None


def _bias_class(score):
    if score is None:
        return "bias-unrated"
    if score <= LEFT_MAX:
        return "bias-left"
    if score <= CENTER_MAX:
        return "bias-center"
    return "bias-right"


def _effective_bias():
    # Article rating, falling back to the outlet's, as story_bias_totals() does.
    return func.coalesce(Article.bias_score, Outlet.bias_score)


def _story_rows(story_ids, window, sort_key, descending, page, text_query):
    eff = _effective_bias()
    article_count = func.count(Article.id)
    # An article with no outlet row counts by its raw source name.
    source_count = func.count(func.distinct(func.coalesce(Outlet.name, Article.source)))
    left = func.sum(case((eff <= LEFT_MAX, 1), else_=0))
    center = func.sum(case((and_(eff > LEFT_MAX, eff <= CENTER_MAX), 1), else_=0))
    right = func.sum(case((eff > CENTER_MAX, 1), else_=0))
    last_update = func.max(Article.date)

    sort_columns = {
        "headline": func.lower(func.coalesce(Story.headline, Story.title)),
        "articles": article_count,
        "sources": source_count,
        "left": left,
        "center": center,
        "right": right,
        "created": Story.created_at,
        "updated": last_update,
    }
    order = _relevance_order(Story.id, story_ids) if sort_key == RELEVANCE else None
    if order is None:
        order = sort_columns[sort_key if sort_key in sort_columns else "updated"]
        order = order.desc() if descending else order.asc()

    query = (
        db.session.query(
            Story,
            article_count.label("article_count"),
            source_count.label("source_count"),
            left.label("left"),
            center.label("center"),
            right.label("right"),
            last_update.label("last_update"),
        )
        .join(Article, Article.story_id == Story.id)
        .outerjoin(Outlet, Outlet.id == Article.outlet_id)
        .group_by(Story.id)
    )
    window_clause = _in_window(Article.date, *window_bounds(window))
    if window_clause is not None:
        # EXISTS, not a join filter: the counts and bias columns stay whole-story.
        query = query.filter(Story.articles.any(window_clause))
    if story_ids is not None:
        query = query.filter(Story.id.in_(story_ids)) if story_ids else query.filter(False)
    if text_query and story_ids is None:
        query = query.filter(_story_text_match(text_query))

    pagination = query.order_by(order.nullslast(), Story.id.desc()).paginate(
        page=page, per_page=SEARCH_PER_PAGE, error_out=False
    )
    items = pagination.items

    story_id_list = [row[0].id for row in items]
    topics_by_story = {}
    if story_id_list:
        topic_rows = (
            db.session.query(story_topics.c.story_id, Topic.name)
            .join(Topic, Topic.id == story_topics.c.topic_id)
            .filter(story_topics.c.story_id.in_(story_id_list))
            .order_by(Topic.name)
            .all()
        )
        for story_id, name in topic_rows:
            topics_by_story.setdefault(story_id, []).append(name)

    rows = []
    for story, count, n_sources, n_left, n_center, n_right, last in items:
        rows.append({
            "id": story.id,
            "headline": story.headline or story.title or f"Story {story.id}",
            "topics": topics_by_story.get(story.id, []),
            "articles": count,
            "sources": n_sources or 0,
            "left": n_left or 0,
            "center": n_center or 0,
            "right": n_right or 0,
            "created": story.created_at,
            "updated": last,
        })
    return rows, pagination


def _article_rows(article_ids, window, sort_key, descending, page, text_query):
    eff = _effective_bias()
    sibling = aliased(Article)
    story_article_count = (
        db.session.query(func.count(sibling.id))
        .filter(sibling.story_id == Article.story_id)
        .correlate(Article)
        .scalar_subquery()
    )
    sort_columns = {
        "title": func.lower(Article.title),
        "outlet": func.lower(Outlet.name),
        "date": Article.date,
        "bias": eff,
        "story": func.lower(Story.title),
        "story_articles": story_article_count,
    }
    order = _relevance_order(Article.id, article_ids) if sort_key == RELEVANCE else None
    if order is None:
        order = sort_columns[sort_key if sort_key in sort_columns else "date"]
        order = order.desc() if descending else order.asc()

    query = (
        db.session.query(
            Article,
            Outlet,
            Story,
            story_article_count.label("story_articles"),
            eff.label("bias"),
        )
        .outerjoin(Outlet, Outlet.id == Article.outlet_id)
        .outerjoin(Story, Story.id == Article.story_id)
    )
    window_clause = _in_window(Article.date, *window_bounds(window))
    if window_clause is not None:
        query = query.filter(window_clause)
    if article_ids is not None:
        query = query.filter(Article.id.in_(article_ids)) if article_ids else query.filter(False)
    if text_query and article_ids is None:
        like = f"%{text_query}%"
        query = query.filter(or_(Article.title.ilike(like), Article.source.ilike(like), Outlet.name.ilike(like)))

    pagination = query.order_by(order.nullslast(), Article.id.desc()).paginate(
        page=page, per_page=SEARCH_PER_PAGE, error_out=False
    )

    rows = []
    for article, outlet, story, story_articles, bias in pagination.items:
        rows.append({
            "id": article.id,
            "title": article.title or f"Article {article.id}",
            "outlet_name": outlet.name if outlet else (article.source or ""),
            "date": article.date,
            "bias": bias,
            "bias_class": _bias_class(bias),
            "story_id": story.id if story else None,
            "story_title": (story.headline or story.title) if story else "",
            "story_articles": story_articles or 0,
        })
    return rows, pagination


def window_bounds(window):
    return window["since"], window["until"]


def _story_text_match(text):
    like = f"%{text}%"
    return or_(
        Story.title.ilike(like),
        Story.headline.ilike(like),
        Story.summary.ilike(like),
        Story.articles.any(Article.title.ilike(like)),
    )


def _sort_links(tab, base_args, sort_key, descending):
    columns = STORY_COLUMNS if tab == "stories" else ARTICLE_COLUMNS
    links = []
    for key, label, numeric, default_dir in columns:
        active = key == sort_key
        if active:
            next_dir = "asc" if descending else "desc"
        else:
            next_dir = default_dir or "desc"
        href = url_for("admin.search_page", tab=tab, **base_args, sort=key, dir=next_dir) if default_dir else None
        links.append({
            "key": key,
            "label": label,
            "numeric": numeric,
            "href": href,
            "active": active,
            "arrow": ("▼" if descending else "▲") if active else "",
        })
    return links


@admin.route("/search")
@login_required
def search_page():
    args = request.args
    tab = args.get("tab", "stories")
    if tab not in SEARCH_TABS:
        tab = "stories"
    text_query = args.get("q", "").strip() or None
    page = max(1, args.get("page", 1, type=int) or 1)
    window = _resolve_window(args)

    sortable = STORY_SORTABLE if tab == "stories" else ARTICLE_SORTABLE
    column_default = "updated" if tab == "stories" else "date"
    default_sort = RELEVANCE if text_query else column_default
    sort_key = args.get("sort", default_sort)
    if sort_key not in sortable and not (sort_key == RELEVANCE and text_query):
        sort_key = default_sort
    descending = args.get("dir", "desc") != "asc"

    base_args = {"range": window["range"]}
    if window["range"] == "custom":
        base_args.update(start=window["start"], end=window["end"])
    if text_query:
        base_args["q"] = text_query

    degraded = False
    candidate_ids = None
    if text_query:
        try:
            if tab == "stories":
                candidate_ids = search_story_ids(
                    text_query, limit=SEARCH_CANDIDATE_LIMIT, since=window["since"], until=window["until"]
                )
            else:
                candidate_ids = search_article_ids(
                    text_query, limit=SEARCH_CANDIDATE_LIMIT, since=window["since"], until=window["until"]
                )
        except SearchUnavailableError as exc:
            logger.warning("Meilisearch unavailable for search page, using SQL match: %s", exc)
            degraded = True
            candidate_ids = None

    if sort_key == RELEVANCE and candidate_ids is None:
        # SQL fallback has no ranking to follow.
        sort_key = column_default

    if tab == "stories":
        rows, pagination = _story_rows(candidate_ids, window, sort_key, descending, page, text_query)
    else:
        rows, pagination = _article_rows(candidate_ids, window, sort_key, descending, page, text_query)

    tab_urls = {
        key: url_for(
            "admin.search_page",
            tab=key,
            **{k: v for k, v in base_args.items()},
        )
        for key in SEARCH_TABS
    }

    def page_url(target_page):
        return url_for("admin.search_page", tab=tab, page=target_page, sort=sort_key,
                       dir="desc" if descending else "asc", **base_args)

    return render_template(
        "search.html",
        tab=tab,
        tab_urls=tab_urls,
        q=text_query or "",
        window=window,
        sort_key=sort_key,
        descending=descending,
        columns=_sort_links(tab, base_args, sort_key, descending),
        rows=rows,
        page=pagination.page,
        total_pages=pagination.pages,
        total=pagination.total,
        prev_url=page_url(pagination.page - 1) if pagination.has_prev else None,
        next_url=page_url(pagination.page + 1) if pagination.has_next else None,
        degraded=degraded,
        sorted_by_relevance=sort_key == RELEVANCE,
        relevance_url=url_for("admin.search_page", tab=tab, sort=RELEVANCE, **base_args)
        if text_query and candidate_ids is not None else None,
        topics=Topic.query.filter_by(is_active=True).order_by(Topic.sort_order).all(),
        time_ranges=[("24h", "24 hours"), ("7d", "7 days"), ("30d", "30 days"), ("all", "All time"), ("custom", "Custom")],
        active_nav="search",
    )
