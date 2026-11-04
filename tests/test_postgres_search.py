"""Search query regression checks; PostgreSQL behavior needs integration tests too."""
from datetime import datetime

from aggregator import search


def test_plain_search_uses_prefix_and_passes_window_before_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(search, '_run', lambda sql, params: calls.append((sql, dict(params))) or [(7,)])
    monkeypatch.setattr(search, '_fuzzy_article_ids', lambda *args: [])
    since, until = datetime(2026, 1, 1), datetime(2026, 2, 1)
    assert search.search_article_ids('Ukraine news', limit=1000, since=since, until=until) == [7]
    sql, params = calls[0]
    assert params == {'q': 'Ukraine & news:*', 'n': 1000, 'since': since, 'until': until}
    assert 'a.date >= :since' in sql and 'a.date < :until' in sql and 'LIMIT :n' in sql


def test_web_syntax_does_not_add_fuzzy_matches(monkeypatch):
    monkeypatch.setattr(search, '_run', lambda sql, params: [(1,)])
    def forbidden(*args):
        raise AssertionError('fuzzy results can violate exclusions and phrases')
    monkeypatch.setattr(search, '_fuzzy_article_ids', forbidden)
    monkeypatch.setattr(search, '_fuzzy_story_ids', forbidden)
    for query in ('Ukraine -Russia', '"New York"', 'Ukraine OR Russia'):
        assert search.search_article_ids(query) == [1]
        assert search.search_story_ids(query) == [1]


def test_query_side_folds_accents_before_matching():
    # Stored vectors are unaccented, so the query must be too: "Mbappe" and
    # "Mbappé" should both find "Mbappé" in the story text.
    sql, params = search._tsquery_sql('Mbappe', True)
    assert 'immutable_unaccent(:q)' in sql
    assert params == {'q': 'Mbappe:*'}


def test_title_matches_rank_first_without_reading_article_text(monkeypatch):
    # Title matches come first (stories: own title, then an article title).
    # Scoring never reads an article's search_vector: it holds the article
    # text and lives out of line, so ts_rank on it made broad words slow.
    calls = []
    monkeypatch.setattr(search, '_run', lambda sql, params: calls.append(sql) or [(1,)] * 20)
    search.search_story_ids('senate')
    search.search_article_ids('senate')
    story_sql, article_sql = calls
    assert 'own.title_hit THEN 2 WHEN art.title_hit THEN 1' in story_sql
    assert 'ORDER BY tier DESC' in story_sql
    assert 'ORDER BY a.title_vector @@ q.tsq DESC' in article_sql
    for sql in calls:
        # The planner would otherwise seq-scan for common words.
        assert 'AS MATERIALIZED' in sql
        assert 'ts_rank(a.search_vector' not in sql
