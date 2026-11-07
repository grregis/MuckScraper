from types import SimpleNamespace

from news_fetcher.summarizer import _select_story_prompt_articles


def _article(title):
    return SimpleNamespace(title=title, content=None)


def _story(title, articles, headline=None):
    return SimpleNamespace(
        id=999,
        title=title,
        headline=headline,
        articles=articles,
    )


def test_story_prompt_filter_excludes_clear_health_policy_outlier():
    good_1 = _article("UnitedHealthcare to remove prior insurance approval requirements for nearly two-thirds of pediatric services")
    good_2 = _article("UnitedHealthcare to remove insurance approval requirements for some pediatric services")
    good_3 = _article("UnitedHealthcare removing prior approval for most pediatric services")
    outlier = _article("Trump directs CDC to align with assessment calling for fewer childhood vaccines")

    selected, excluded = _select_story_prompt_articles(
        _story("UnitedHealthcare removing prior approval for most pediatric services", [good_1, good_2, outlier, good_3]),
        limit=10,
    )

    assert outlier in excluded
    assert good_1 in selected
    assert good_2 in selected
    assert good_3 in selected


def test_story_prompt_filter_excludes_unrelated_immigration_crime_outlier():
    good_1 = _article("ICE agent accused of shooting man in north Minneapolis arrested in Texas")
    good_2 = _article("ICE agent charged in Minneapolis shooting is arrested in Texas")
    good_3 = _article("ICE officer charged in nonfatal Minnesota shooting arrested")
    outlier = _article("Sheridan Gorman's alleged illegal immigrant killer caught with weapon while in jail custody")

    selected, excluded = _select_story_prompt_articles(
        _story("ICE agent charged in Minneapolis shooting is arrested in Texas", [good_1, good_2, outlier, good_3]),
        limit=10,
    )

    assert outlier in excluded
    assert good_1 in selected
    assert good_2 in selected
    assert good_3 in selected


def test_story_prompt_filter_keeps_small_clusters_unchanged():
    first = _article("UnitedHealthcare removing prior approval for most pediatric services")
    second = _article("Trump directs CDC to align with assessment calling for fewer childhood vaccines")

    selected, excluded = _select_story_prompt_articles(
        _story("UnitedHealthcare removing prior approval for most pediatric services", [first, second]),
        limit=10,
    )

    assert selected == [first, second]
    assert excluded == []
