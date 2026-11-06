import os
import pytest
from unittest.mock import patch, MagicMock

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key-1234567890abcdef1234567890abcdef"

from aggregator import create_app, db
from aggregator.models import Topic, Outlet, Story, RssFeed
from news_fetcher.fetch_and_store_articles.ingestion import store_articles
from news_fetcher.scraper import ScrapeResult
from news_fetcher import rss_fetcher


@pytest.fixture
def app_ctx():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.create_all()
        # Seed test outlets and topics
        outlet = Outlet(name="Test Wire", url="https://example.com", description="N/A")
        db.session.add(outlet)
        sports_topic = Topic(name="Sports", is_active=True)
        db.session.add(sports_topic)
        politics_topic = Topic(name="US Politics", is_active=True)
        db.session.add(politics_topic)
        archived_topic = Topic(name="Archived Topic", is_active=False)
        db.session.add(archived_topic)
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


def _make_article(url, title="Breaking News Headline"):
    return {
        "title": title,
        "url": url,
        "content": "Article body snippet for testing topic assignment.",
        "source_name": "Test Wire",
    }


def test_opted_in_feed_matching_active_topic_tags_story(app_ctx):
    """An opted-in feed whose label matches an active topic tags the story."""
    article = _make_article("https://example.com/sports-story-1", "Championship Game Highlights")
    with patch("news_fetcher.fetch_and_store_articles.ingestion.get_embedding", return_value=[0.1] * 768), \
         patch("news_fetcher.fetch_and_store_articles.ingestion.classify_article", return_value=[]), \
         patch("news_fetcher.fetch_and_store_articles.ingestion.scrape_article", return_value=ScrapeResult(content=None, status="skipped")):

        store_articles([article], "Sports", provider="rss", assign_topic="Sports")

    story = Story.query.first()
    assert story is not None
    assert "Sports" in [t.name for t in story.topics]


def test_opted_out_feed_does_not_tag_story(app_ctx):
    """An opted-out feed (assign_topic=None) does not attach its label to the story."""
    article = _make_article("https://example.com/sports-story-2", "General News From Sports Feed")
    with patch("news_fetcher.fetch_and_store_articles.ingestion.get_embedding", return_value=[0.1] * 768), \
         patch("news_fetcher.fetch_and_store_articles.ingestion.classify_article", return_value=[]), \
         patch("news_fetcher.fetch_and_store_articles.ingestion.scrape_article", return_value=ScrapeResult(content=None, status="skipped")):

        store_articles([article], "Sports", provider="rss", assign_topic=None)

    story = Story.query.first()
    assert story is not None
    assert "Sports" not in [t.name for t in story.topics]


def test_unknown_label_does_not_tag_story(app_ctx):
    """A feed label that doesn't match an existing topic does not tag the story."""
    article = _make_article("https://example.com/story-3", "Article from Custom Feed")
    with patch("news_fetcher.fetch_and_store_articles.ingestion.get_embedding", return_value=[0.1] * 768), \
         patch("news_fetcher.fetch_and_store_articles.ingestion.classify_article", return_value=[]), \
         patch("news_fetcher.fetch_and_store_articles.ingestion.scrape_article", return_value=ScrapeResult(content=None, status="skipped")):

        store_articles([article], "Custom Label", provider="rss", assign_topic="Custom Label")

    story = Story.query.first()
    assert story is not None
    assert len(story.topics) == 0


def test_inactive_topic_does_not_tag_story(app_ctx):
    """An inactive topic (is_active=False) matching the feed label is not attached."""
    article = _make_article("https://example.com/story-4", "Article from Inactive Topic Feed")
    with patch("news_fetcher.fetch_and_store_articles.ingestion.get_embedding", return_value=[0.1] * 768), \
         patch("news_fetcher.fetch_and_store_articles.ingestion.classify_article", return_value=[]), \
         patch("news_fetcher.fetch_and_store_articles.ingestion.scrape_article", return_value=ScrapeResult(content=None, status="skipped")):

        store_articles([article], "Archived Topic", provider="rss", assign_topic="Archived Topic")

    story = Story.query.first()
    assert story is not None
    assert "Archived Topic" not in [t.name for t in story.topics]


def test_newsapi_fetch_labelled_sports_does_not_tag_story(app_ctx):
    """A NewsAPI scheduled fetch passing topic_name='Sports' without assign_topic does not attach topic."""
    article = _make_article("https://example.com/newsapi-sports-1", "NewsAPI Query Result")
    with patch("news_fetcher.fetch_and_store_articles.ingestion.get_embedding", return_value=[0.1] * 768), \
         patch("news_fetcher.fetch_and_store_articles.ingestion.classify_article", return_value=[]), \
         patch("news_fetcher.fetch_and_store_articles.ingestion.scrape_article", return_value=ScrapeResult(content=None, status="skipped")):

        # Scheduled API fetches call store_articles(normalized, topic_name, provider="newsapi")
        store_articles([article], "Sports", provider="newsapi")

    story = Story.query.first()
    assert story is not None
    assert "Sports" not in [t.name for t in story.topics]


def test_rss_fetch_assigns_topic_only_for_opted_in_feed(app_ctx):
    """fetch_and_store_rss passes assign_topic only when feed.assign_topic is True."""
    feed_opted_in = RssFeed(
        url="https://example.com/feed-opted-in.xml",
        bucket="general",
        label="Sports",
        enabled=True,
        assign_topic=True,
    )
    feed_opted_out = RssFeed(
        url="https://example.com/feed-opted-out.xml",
        bucket="general",
        label="Sports",
        enabled=True,
        assign_topic=False,
    )
    db.session.add_all([feed_opted_in, feed_opted_out])
    db.session.commit()

    captured_calls = []

    def fake_store(articles, topic_tag, provider=None, assign_topic=None):
        captured_calls.append({"topic_tag": topic_tag, "assign_topic": assign_topic})
        return {"input_articles": len(articles), "stored": len(articles)}

    with patch.object(rss_fetcher, "_active_feeds", return_value=[feed_opted_in, feed_opted_out]), \
         patch.object(rss_fetcher, "_fetch_with_fallback", return_value=("Source", [_make_article("https://example.com/art1")])), \
         patch("news_fetcher.fetch_and_store_articles.store_articles", side_effect=fake_store):

        rss_fetcher.fetch_and_store_rss()

    assert len(captured_calls) == 2
    assert captured_calls[0] == {"topic_tag": "Sports", "assign_topic": "Sports"}
    assert captured_calls[1] == {"topic_tag": "Sports", "assign_topic": None}
