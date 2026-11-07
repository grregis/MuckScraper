"""publish_edition() prefers stories from two or more OUTLETS (2026-10-06):
one outlet's pair (60 Minutes transcript + write-up, Fox promo runs) no longer
counts as multi-source."""
from types import SimpleNamespace as NS

from news_fetcher.fetch_and_store_articles.edition_publish import _story_outlet_count


def story(*articles):
    return NS(articles=[NS(outlet_id=o, source=s) for o, s in articles])


def test_one_outlet_pair_counts_once():
    assert _story_outlet_count(story((7, "CBS News"), (7, "CBS News"))) == 1


def test_two_outlets_count_twice():
    assert _story_outlet_count(story((7, "CBS News"), (9, "Fox News"))) == 2


def test_articles_without_outlet_fall_back_to_source_name():
    assert _story_outlet_count(story((None, "CBS News"), (None, "cbs news "))) == 1
    assert _story_outlet_count(story((None, "CBS News"), (9, "Fox News"))) == 2


def test_empty_story():
    assert _story_outlet_count(story()) == 0
