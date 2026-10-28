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
