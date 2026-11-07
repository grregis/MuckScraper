import unittest
from types import SimpleNamespace

try:
    from news_fetcher.rss_fetcher import (
        _feed_article_matches_story,
        _story_needs_right_enrichment,
    )
except ModuleNotFoundError as exc:
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


def make_article(title, bias_score=None, outlet_bias_score=None):
    outlet = None
    if outlet_bias_score is not None:
        outlet = SimpleNamespace(bias_score=outlet_bias_score)
    return SimpleNamespace(title=title, bias_score=bias_score, outlet=outlet)


def make_story(title, articles, headline=None):
    display_headline = headline or title
    return SimpleNamespace(
        title=title,
        headline=headline,
        display_headline=display_headline,
        articles=articles,
    )


class RssEnrichmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    def test_story_needs_right_enrichment_for_left_leaning_multi_source_story(self):
        story = make_story(
            "Trump halts planned Iran attack after Gulf states intervene",
            [
                make_article("Trump halts planned Iran attack after Gulf states intervene", outlet_bias_score=2),
                make_article("Gulf states intervene as Trump pauses Iran attack", outlet_bias_score=2),
                make_article("Iran strike delayed after regional pressure", outlet_bias_score=3),
            ],
        )

        self.assertTrue(_story_needs_right_enrichment(story))

    def test_story_does_not_need_right_enrichment_when_right_coverage_exists(self):
        story = make_story(
            "WHO declares Ebola outbreak global health emergency",
            [
                make_article("WHO declares Ebola outbreak global health emergency", outlet_bias_score=2),
                make_article("Global health emergency declared over Ebola outbreak", outlet_bias_score=3),
                make_article("Ebola emergency draws swift response", outlet_bias_score=5),
            ],
        )

        self.assertFalse(_story_needs_right_enrichment(story))

    def test_feed_article_matches_story_for_same_event(self):
        story = make_story(
            "Cuba warns US of bloodbath over drone claims",
            [
                make_article("Cuba warns US of bloodbath over drone claims", outlet_bias_score=2),
                make_article("Havana says drone accusations could trigger bloodbath", outlet_bias_score=2),
            ],
        )
        article_data = {
            "title": "The Hill: Cuba warns U.S. drone claims risk bloodbath",
        }

        self.assertTrue(_feed_article_matches_story(article_data, story))

    def test_feed_article_does_not_match_unrelated_story(self):
        story = make_story(
            "Supreme Court sends Native American voting rights decision back to lower court",
            [
                make_article("Supreme Court sends Native American voting rights case back", outlet_bias_score=3),
                make_article("Voting rights dispute returned to lower court", outlet_bias_score=2),
            ],
        )
        article_data = {
            "title": "Manchester City prepares for Guardiola departure after trophy run",
        }

        self.assertFalse(_feed_article_matches_story(article_data, story))


if __name__ == "__main__":
    unittest.main()
