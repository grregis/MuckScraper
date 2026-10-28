"""Full-text search over stories and articles, in Postgres.

Each story and article row carries a `search_vector` column that Postgres
generates from its text (aggregator/models.py), with a GIN index on it, so
search is always in step with the data and there is nothing to rebuild.
Weights: titles and headlines A, summaries B, source and deep report C,
article text D. Tags in stored HTML are dropped by the text parser.

Matching is by English word stems, with the last word of a plain query also
matched as a prefix ("ukra" finds Ukraine) so as-you-type search works.
Quoted phrases, OR and -word use web-search syntax instead. When word matching
finds few hits, titles are also matched by trigram word similarity (pg_trgm,
`%>` at its default 0.6 threshold), which catches typos and partial names.

Replaced Meilisearch (2026-10-22): about 120,000 articles per six months is
small for Postgres, every search already ended in a Postgres query, and the
separate index needed a sync after every run and an 80-minute rebuild.
"""
import logging
import re

from sqlalchemy.exc import SQLAlchemyError

from aggregator.models import db

logger = logging.getLogger(__name__)

TEXT_CONFIG = "english"
MAX_QUERY_CHARS = 200
# Below this many word matches, trigram title matches are added after them.
FUZZY_TOP_UP_BELOW = 10
# Article hits count for less than the story's own title/summary hits when
# ranking stories, plus a small bonus for every further matching article.
ARTICLE_HIT_WEIGHT = 0.5
EXTRA_ARTICLE_BONUS = 0.05

_WORD = re.compile(r"\w+", re.UNICODE)
_WEB_SYNTAX = re.compile(r'"|(^|\s)-\w|\bOR\b')


class SearchUnavailableError(RuntimeError):
    """The search query failed (e.g. the search columns are missing because
    migrations have not run). Callers fall back to a plain substring match."""


def _clean(query):
    return (query or "").strip()[:MAX_QUERY_CHARS]


def _tsquery_sql(query, prefix):
    """(SQL for a tsquery, params). Web-search syntax when the query uses it,
    otherwise all words required with the last one as a prefix."""
    if _WEB_SYNTAX.search(query):
        return f"websearch_to_tsquery('{TEXT_CONFIG}', :q)", {"q": query}
    words = _WORD.findall(query)
    if not words:
        return None, {}
    terms = list(words)
    if prefix:
        terms[-1] = f"{terms[-1]}:*"
    return f"to_tsquery('{TEXT_CONFIG}', :q)", {"q": " & ".join(terms)}


def _window_sql(column, since, until, params):
    clauses = []
    if since is not None:
        clauses.append(f"{column} >= :since")
        params["since"] = since
    if until is not None:
        clauses.append(f"{column} < :until")
        params["until"] = until
    return clauses


def _run(sql, params):
    try:
        return db.session.execute(db.text(sql), params).fetchall()
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise SearchUnavailableError(str(exc).splitlines()[0]) from exc


def _ordered_unique(ids, limit):
    seen = set()
    ordered = []
    for row_id in ids:
        if row_id is not None and row_id not in seen:
            seen.add(row_id)
            ordered.append(row_id)
            if len(ordered) >= limit:
                break
    return ordered


def _fuzzy_story_ids(query, limit, since, until, exclude):
    params = {"q": query, "n": limit}
    window = _window_sql("a.date", since, until, params)
    window_sql = (
        "AND EXISTS (SELECT 1 FROM articles a WHERE a.story_id = s.id AND " + " AND ".join(window) + ")"
        if window else ""
    )
    rows = _run(
        f"""
        SELECT s.id
        FROM stories s
        WHERE (s.title %> :q OR s.headline %> :q)
          {window_sql}
        ORDER BY greatest(word_similarity(:q, s.title), word_similarity(:q, coalesce(s.headline, ''))) DESC,
                 s.id DESC
        LIMIT :n
        """,
        params,
    )
    return [row[0] for row in rows if row[0] not in exclude]


def _fuzzy_article_ids(query, limit, since, until, exclude):
    params = {"q": query, "n": limit}
    window = _window_sql("a.date", since, until, params)
    window_sql = ("AND " + " AND ".join(window)) if window else ""
    rows = _run(
        f"""
        SELECT a.id
        FROM articles a
        WHERE a.title %> :q
          {window_sql}
        ORDER BY word_similarity(:q, a.title) DESC, a.date DESC NULLS LAST
        LIMIT :n
        """,
        params,
    )
    return [row[0] for row in rows if row[0] not in exclude]


def search_story_ids(query, limit=250, since=None, until=None, prefix=True):
    """Story ids matching `query`, best first, at most `limit`.

    A story matches on its own title, headline, summary or deep report, or
    when any of its articles matches. `since`/`until` (naive UTC; inclusive,
    exclusive; either optional) keep only stories with an article published
    in the window, the same rule as the story listings.
    """
    query = _clean(query)
    if not query:
        return []
    tsquery, params = _tsquery_sql(query, prefix)
    ids = []
    if tsquery:
        params.update(n=limit, article_weight=ARTICLE_HIT_WEIGHT, bonus=EXTRA_ARTICLE_BONUS)
        article_window = _window_sql("a.date", since, until, params)
        article_window_sql = ("AND " + " AND ".join(article_window)) if article_window else ""
        story_window_sql = (
            "AND EXISTS (SELECT 1 FROM articles a WHERE a.story_id = s.id "
            + article_window_sql + ")"
            if article_window else ""
        )
        rows = _run(
            f"""
            WITH q AS (SELECT {tsquery} AS tsq),
            hits AS (
                SELECT s.id AS story_id, ts_rank(s.search_vector, q.tsq) AS rank
                FROM stories s, q
                WHERE s.search_vector @@ q.tsq {story_window_sql}
                UNION ALL
                SELECT a.story_id, ts_rank(a.search_vector, q.tsq) * :article_weight
                FROM articles a, q
                WHERE a.search_vector @@ q.tsq AND a.story_id IS NOT NULL {article_window_sql}
            )
            SELECT story_id, max(rank) + :bonus * ln(count(*)) AS score
            FROM hits
            GROUP BY story_id
            ORDER BY score DESC, story_id DESC
            LIMIT :n
            """,
            params,
        )
        ids = [row[0] for row in rows]
    if not _WEB_SYNTAX.search(query) and len(ids) < min(FUZZY_TOP_UP_BELOW, limit):
        ids += _fuzzy_story_ids(query, limit - len(ids), since, until, set(ids))
    return _ordered_unique(ids, limit)


def search_article_ids(query, limit=250, since=None, until=None, prefix=True):
    """Article ids matching `query`, best first, at most `limit`, optionally
    bounded by publish date (`since` inclusive, `until` exclusive)."""
    query = _clean(query)
    if not query:
        return []
    tsquery, params = _tsquery_sql(query, prefix)
    ids = []
    if tsquery:
        params["n"] = limit
        window = _window_sql("a.date", since, until, params)
        window_sql = ("AND " + " AND ".join(window)) if window else ""
        rows = _run(
            f"""
            WITH q AS (SELECT {tsquery} AS tsq)
            SELECT a.id
            FROM articles a, q
            WHERE a.search_vector @@ q.tsq {window_sql}
            ORDER BY ts_rank(a.search_vector, q.tsq) DESC, a.date DESC NULLS LAST
            LIMIT :n
            """,
            params,
        )
        ids = [row[0] for row in rows]
    if not _WEB_SYNTAX.search(query) and len(ids) < min(FUZZY_TOP_UP_BELOW, limit):
        ids += _fuzzy_article_ids(query, limit - len(ids), since, until, set(ids))
    return _ordered_unique(ids, limit)


def search_ready():
    """Whether the search columns and indexes exist (migrations applied)."""
    rows = db.session.execute(db.text(
        """
        SELECT indexname FROM pg_indexes
        WHERE indexname IN ('ix_articles_search_vector', 'ix_stories_search_vector',
                            'ix_articles_title_trgm', 'ix_stories_title_trgm', 'ix_stories_headline_trgm')
        """
    )).fetchall()
    return len(rows) == 5
