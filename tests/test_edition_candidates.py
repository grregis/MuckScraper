"""Which stories can go into an edition (edition_publish.edition_candidate_query).

A story qualifies when it gained an article in the last 3 days and started
within 7. Before 2026-11-12 the rule was the story's own start within 3 days,
so a long-running story with fresh coverage dropped out of every edition.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

NOW = datetime(2026, 11, 12, 6, 0)


@pytest.fixture()
def app():
    from aggregator import create_app, db
    with patch.dict("os.environ", SECRET_KEY="edition-candidates-fixture", DATABASE_URL="sqlite://"):
        app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _story(title, created_days_ago, article_days_ago):
    from aggregator import db
    from aggregator.models import Article, Story
    story = Story(title=title, created_at=NOW - timedelta(days=created_days_ago))
    db.session.add(story)
    db.session.flush()
    for n, days in enumerate(article_days_ago):
        db.session.add(Article(
            title=f"{title} {n}", url=f"https://example.com/{title}/{n}",
            story_id=story.id, fetched_at=NOW - timedelta(days=days),
        ))
    db.session.flush()
    return story


def test_old_story_with_fresh_coverage_qualifies(app):
    from news_fetcher.fetch_and_store_articles.edition_publish import edition_candidate_query
    long_running = _story("long-running", 6, [6, 5, 0.2])
    new = _story("new", 1, [1])
    query, _ = edition_candidate_query(NOW)
    ids = {s.id for s in query.all()}
    assert ids == {long_running.id, new.id}


def test_quiet_story_and_too_old_story_are_left_out(app):
    from news_fetcher.fetch_and_store_articles.edition_publish import edition_candidate_query
    _story("quiet", 5, [5, 4])            # nothing in the last 3 days
    _story("too-old", 8, [8, 0.1])        # fresh article, but started 8 days ago
    query, _ = edition_candidate_query(NOW)
    assert query.all() == []


def test_newest_article_column_orders_by_recency(app):
    from news_fetcher.fetch_and_store_articles.edition_publish import edition_candidate_query
    older = _story("older-news", 2, [2])
    fresher = _story("old-story-fresh-news", 5, [5, 0.1])
    query, latest = edition_candidate_query(NOW)
    assert [s.id for s in query.order_by(latest.desc()).all()] == [fresher.id, older.id]
