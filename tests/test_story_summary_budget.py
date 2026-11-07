from news_fetcher.summarizer import (
    STORY_SUMMARY_CONTENT_BUDGET as BUDGET,
    STORY_SUMMARY_EXCERPT_MAX as CAP,
    _story_summary_excerpt_limits as limits,
)


def test_small_story_unchanged():
    assert limits([5000] * 6) == [CAP] * 6


def test_ten_full_articles_share_the_budget():
    got = limits([5000] * 10)
    assert sum(got) <= BUDGET
    assert got == [BUDGET // 10] * 10


def test_stubs_give_their_share_to_long_articles():
    got = limits([200, 0, 300] + [5000] * 7)
    assert got[:3] == [200, 0, 300]
    assert sum(got) <= BUDGET
    assert min(got[3:]) == (BUDGET - 500) // 7


def test_never_exceeds_cap_or_content():
    got = limits([100, 9000, 1200])
    assert got == [100, CAP, 1200]


def test_empty():
    assert limits([]) == []
