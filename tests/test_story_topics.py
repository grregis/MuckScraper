"""A story's topics come from its articles (story_topics, 2026-11-17).

Before this, topics only accumulated: a story kept every topic any article
was ever classified into, and each new article inherited the story's whole
list. The FAA ground-stop story reached all 7 topics (Sports included).
"""
from unittest.mock import patch

import pytest

from news_fetcher.fetch_and_store_articles.story_topics import story_topic_choice


def test_small_story_keeps_every_topic():
    # 4 articles: a quarter is one article, so any topic counts.
    assert set(story_topic_choice([["US Politics"], ["US News"], ["US Politics"], ["Sports"]])) == {
        "US Politics", "US News", "Sports"}


def test_big_story_drops_topics_few_articles_have():
    # The FAA case: 20 articles, mostly US News, a couple tagged Sports.
    lists = [["US News"]] * 12 + [["US News", "US Politics"]] * 6 + [["Sports"]] * 2
    assert story_topic_choice(lists) == ["US News", "US Politics"]


def test_most_common_topic_always_kept():
    lists = [["Sci/Tech"]] * 3 + [["Buss/Fin"]] * 2 + [["US News"]] * 2 + [["Sports"]] * 2 + [["Other"]] * 3
    chosen = story_topic_choice(lists)
    assert "Sci/Tech" in chosen


def test_other_dropped_when_a_real_topic_qualifies():
    assert story_topic_choice([["Other"], ["Other"], ["US News"]]) == ["US News"]
    assert story_topic_choice([["Other"], ["Other"]]) == ["Other"]


def test_articles_without_topics_dont_count():
    assert story_topic_choice([[], [], ["US News"]]) == ["US News"]
    assert story_topic_choice([[], []]) == []


@pytest.fixture()
def app():
    from aggregator import create_app, db
    with patch.dict("os.environ", SECRET_KEY="story-topics-fixture", DATABASE_URL="sqlite://"):
        app = create_app()
    app.config.update(TESTING=True)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_refresh_story_topics_follows_the_articles(app):
    from aggregator import db
    from aggregator.models import Article, Story, Topic
    from news_fetcher.fetch_and_store_articles.story_topics import refresh_story_topics

    names = ["US Politics", "US News", "Sports", "Sci/Tech", "Buss/Fin", "International News", "Other"]
    topics = {n: Topic(name=n) for n in names}
    db.session.add_all(topics.values())
    story = Story(title="FAA ground stop")
    story.topics = list(topics.values())  # the snowballed state
    db.session.add(story)
    for i in range(8):
        article_topics = [topics["US News"]] if i < 6 else [topics["Sports"]]
        db.session.add(Article(title=f"a{i}", url=f"https://example.com/{i}", story=story, topics=article_topics))
    db.session.flush()

    refresh_story_topics(story)
    db.session.flush()
    assert {t.name for t in story.topics} == {"US News", "Sports"}  # Sports: 2 of 8 = a quarter

    db.session.add(Article(title="a8", url="https://example.com/8", story=story, topics=[topics["US News"]]))
    db.session.flush()
    refresh_story_topics(story)
    assert {t.name for t in story.topics} == {"US News"}  # Sports: 2 of 9 < a quarter


def test_refresh_keeps_topics_when_no_article_has_any(app):
    from aggregator import db
    from aggregator.models import Article, Story, Topic
    from news_fetcher.fetch_and_store_articles.story_topics import refresh_story_topics

    news = Topic(name="US News")
    story = Story(title="old story", topics=[news])
    db.session.add_all([news, story, Article(title="x", url="https://example.com/x", story=story)])
    db.session.flush()
    refresh_story_topics(story)
    assert [t.name for t in story.topics] == ["US News"]
