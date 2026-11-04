"""Full-text search over stories and articles, in Postgres.

Each story and article row carries a `search_vector` column that Postgres
generates from its text (aggregator/models.py), with a GIN index on it, so
search is always in step with the data and there is nothing to rebuild.
Weights: titles and headlines A, summaries B, source and deep report C,
article text D. Tags in stored HTML are dropped by the text parser.

Ranking puts title matches first: a story whose own title or headline has
every word, then a story with an article whose title has them, then the
rest. Articles likewise: title matches first, then newest first. Titles are
checked against a small `title_vector` column. An article's `search_vector`
holds up to 6,000 characters of text, so Postgres stores it out of line, and
reading it back for every match (to score it with ts_rank) was most of the
time a broad word such as "senate" took. Matching only uses the GIN index,
which never reads it.

Matching is by English word stems, with the last word of a plain query also
matched as a prefix ("ukra" finds Ukraine) so as-you-type search works.
Accents are folded on both sides (immutable_unaccent, migration e6f2a9c4b701),
so "Mbappe" finds "Mbappé". Quoted phrases, OR and -word use web-search syntax
instead. When word matching finds few hits, titles are also matched by trigram
word similarity (pg_trgm, `%>` at 0.5 rather than its default 0.6), which
catches typos and partial names such as "Olesky" for Oleshky.

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
# Trigram word similarity for the typo fallback. pg_trgm's default is 0.6, which
# misses one-letter typos in names ("Olesky" against "Oleshky" scores 0.57).
FUZZY_THRESHOLD = 0.5
# Within a tier, a story's score is its own text rank plus this much per
# doubling of its matching articles, so a story many outlets mention in
# passing still rises above one that mentions the words once.
ARTICLE_COUNT_BONUS = 0.05

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
        return f"websearch_to_tsquery('{TEXT_CONFIG}', immutable_unaccent(:q))", {"q": query}
    words = _WORD.findall(query)
    if not words:
        return None, {}
    terms = list(words)
    if prefix:
        terms[-1] = f"{terms[-1]}:*"
    return f"to_tsquery('{TEXT_CONFIG}', immutable_unaccent(:q))", {"q": " & ".join(terms)}


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


def _set_fuzzy_threshold(value):
    # SET returns no rows, so it can't go through _run()'s fetchall().
    try:
        db.session.execute(db.text(f"SET LOCAL pg_trgm.word_similarity_threshold = {value}"))
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise SearchUnavailableError(str(exc).splitlines()[0]) from exc


def _run_fuzzy(sql, params):
    """_run() with the typo threshold for this transaction only."""
    _set_fuzzy_threshold(FUZZY_THRESHOLD)
    try:
        return _run(sql, params)
    finally:
        _set_fuzzy_threshold("DEFAULT")


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
    rows = _run_fuzzy(
        f"""
        SELECT s.id
        FROM stories s
        WHERE (s.title %> immutable_unaccent(:q) OR s.headline %> immutable_unaccent(:q))
          {window_sql}
        ORDER BY greatest(word_similarity(immutable_unaccent(:q), s.title),
                          word_similarity(immutable_unaccent(:q), coalesce(s.headline, ''))) DESC,
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
    rows = _run_fuzzy(
        f"""
        SELECT a.id
        FROM articles a
        WHERE a.title %> immutable_unaccent(:q)
          {window_sql}
        ORDER BY word_similarity(immutable_unaccent(:q), a.title) DESC, a.date DESC NULLS LAST
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
        params.update(n=limit, bonus=ARTICLE_COUNT_BONUS)
        article_window = _window_sql("a.date", since, until, params)
        article_window_sql = ("AND " + " AND ".join(article_window)) if article_window else ""
        story_window_sql = (
            "AND EXISTS (SELECT 1 FROM articles a WHERE a.story_id = s.id "
            + article_window_sql + ")"
            if article_window else ""
        )
        # MATERIALIZED hides the query from the planner. When it can see it,
        # it picks a sequential scan for a common word such as "trump", which
        # reads every article's out-of-line search_vector to test it and takes
        # several times longer than the GIN index does.
        # A story whose own title matches is always in `own` (its
        # search_vector holds the title too), so the full join needs no
        # second look at stories that matched only through articles.
        rows = _run(
            f"""
            WITH q AS MATERIALIZED (SELECT {tsquery} AS tsq),
            own AS (
                SELECT s.id AS story_id,
                       s.title_vector @@ q.tsq AS title_hit,
                       ts_rank(s.search_vector, q.tsq) AS rank
                FROM stories s, q
                WHERE s.search_vector @@ q.tsq {story_window_sql}
            ),
            art AS (
                SELECT a.story_id, count(*) AS n, bool_or(a.title_vector @@ q.tsq) AS title_hit
                FROM articles a, q
                WHERE a.search_vector @@ q.tsq AND a.story_id IS NOT NULL {article_window_sql}
                GROUP BY a.story_id
            )
            SELECT coalesce(own.story_id, art.story_id) AS story_id,
                   CASE WHEN own.title_hit THEN 2 WHEN art.title_hit THEN 1 ELSE 0 END AS tier,
                   coalesce(own.rank, 0) + :bonus * ln(1 + coalesce(art.n, 0)) / ln(2) AS score
            FROM own FULL JOIN art ON art.story_id = own.story_id
            ORDER BY tier DESC, score DESC, story_id DESC
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
        # MATERIALIZED: see search_story_ids.
        rows = _run(
            f"""
            WITH q AS MATERIALIZED (SELECT {tsquery} AS tsq)
            SELECT a.id
            FROM articles a, q
            WHERE a.search_vector @@ q.tsq {window_sql}
            ORDER BY a.title_vector @@ q.tsq DESC, a.date DESC NULLS LAST, a.id DESC
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
