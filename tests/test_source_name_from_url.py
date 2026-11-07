"""Domain overrides for outlets whose feed title isn't the outlet name."""
import unittest

from news_fetcher.allsides_lookup import get_allsides_score
from news_fetcher.fetch_and_store_articles.outlets import normalize_source_name, source_name_from_url


class SourceNameFromUrlTests(unittest.TestCase):
    def test_latimes_opinion_feed(self):
        # The opinion feed is titled "Opinion Voices" (2026-10-09).
        self.assertEqual(
            source_name_from_url("https://www.latimes.com/opinion/story/2026-10-08/x"),
            "Los Angeles Times")

    def test_washington_post(self):
        self.assertEqual(
            source_name_from_url("https://www.washingtonpost.com/opinions/2026/10/08/x/"),
            "Washington Post")

    def test_outlets_that_missed_their_allsides_name(self):
        # 2026-10-18: each was AI-rated under its feed title instead.
        cases = {
            "https://reason.com/2026/10/18/x/": ("Reason Magazine", "Reason", 4.0),
            "https://www.newsmax.com/newsfront/x/2026/10/18/id/1/": ("Newsmax - Newsfront", "Newsmax", 5.0),
            "https://www.dailywire.com/news/x": ("The Daily Wire - Breaking News, Videos & Podcasts",
                                                 "The Daily Wire", 5.0),
        }
        self.assertEqual(source_name_from_url("https://www.politico.com/news/2026/10/18/x"), "Politico")
        self.assertEqual(get_allsides_score("Politico"), 2.0)
        for url, (feed_title, name, score) in cases.items():
            self.assertEqual(source_name_from_url(url), name)
            self.assertEqual(normalize_source_name(feed_title), name)
            self.assertEqual(get_allsides_score(name), score)

    def test_unknown_domain(self):
        self.assertIsNone(source_name_from_url("https://example.com/a"))


if __name__ == "__main__":
    unittest.main()
