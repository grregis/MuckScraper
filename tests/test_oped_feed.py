"""Op/Ed feeds (RssFeed bucket `op_ed`, 2026-10-09): every entry is stored as
opinion whatever its URL, but blocks and the other low-value reasons still
apply, and nothing goes to `articles`."""
import unittest
from unittest import mock

from news_fetcher.fetch_and_store_articles import ingestion
from news_fetcher.fetch_and_store_articles.outlets import source_name_from_url


def entry(title, url, source="Some Feed Title"):
    return {"title": title, "url": url, "source_name": source, "content": "<p>x</p>"}


class StoreOpinionFeedTests(unittest.TestCase):
    def run_feed(self, entries, blocks=([], [])):
        saved = []

        def fake_save(title, url, source_name, published_at, content, image_url, seen):
            if url in seen:
                return False
            seen.add(url)
            saved.append((title, url, source_name))
            return True

        with mock.patch.object(ingestion, "get_ingestion_blocks", return_value=blocks), \
             mock.patch.object(ingestion, "save_opinion_piece", side_effect=fake_save), \
             mock.patch.object(ingestion, "db"), \
             mock.patch.object(ingestion, "store_articles") as store_articles:
            metrics = ingestion.store_opinion_feed(entries)
            store_articles.assert_not_called()
        return metrics, saved

    def test_column_without_opinion_in_url_is_saved(self):
        metrics, saved = self.run_feed([
            entry("Biden's legacy is being rewritten",
                  "https://www.washingtontimes.com/news/2026/oct/8/bidens-legacy/",
                  "The Washington Times"),
        ])
        self.assertEqual(metrics["saved"], 1)
        self.assertEqual(saved[0][2], "The Washington Times")

    def test_other_low_value_reasons_still_skip(self):
        metrics, saved = self.run_feed([
            entry("Chiefs vs. Bills odds, predictions and best bets", "https://nypost.com/2026/10/08/odds/"),
            entry("Watch: the panel reacts", "https://www.foxnews.com/video/6380000000"),
        ])
        self.assertEqual(saved, [])
        self.assertEqual(metrics["skipped"].get("betting"), 1)

    def test_seeking_alpha_contributor_still_refused(self):
        metrics, saved = self.run_feed([
            entry("Why I Am Buying REITs", "https://seekingalpha.com/article/4800000-reits"),
        ])
        self.assertEqual(saved, [])
        self.assertEqual(metrics["skipped"], {"low_value_url": 1})

    def test_blocks_apply(self):
        metrics, saved = self.run_feed(
            [entry("Dear Prudence column on in-laws", "https://example.com/a"),
             entry("A fine column", "https://blocked.example/b")],
            blocks=(["blocked.example"], ["dear prudence"]))
        self.assertEqual(saved, [])
        self.assertEqual(metrics["skipped"], {"blocked_title": 1, "blocked_source": 1})

    def test_duplicate_in_feed_counted_once(self):
        e = entry("Same column", "https://nypost.com/2026/10/08/opinion/same/")
        metrics, saved = self.run_feed([e, dict(e)])
        self.assertEqual(metrics["saved"], 1)
        self.assertEqual(metrics["skipped"], {"not_saved": 1})

    def test_feed_title_replaced_by_outlet_name(self):
        _, saved = self.run_feed([
            entry("A column", "https://nypost.com/2026/10/08/opinion/a/",
                  "Opinion – Latest Op-Eds & News Commentary | New York Post"),
        ])
        self.assertEqual(saved[0][2], "New York Post")


class NationalReviewTests(unittest.TestCase):
    def test_whole_site_is_opinion(self):
        from aggregator.article_signals import low_value_article_reason
        self.assertEqual(low_value_article_reason(
            "Justice Alito, Properly Ordered",
            "https://www.nationalreview.com/2026/10/justice-alito-properly-ordered/"), "opinion")

    def test_other_review_sites_untouched(self):
        from aggregator.article_signals import is_opinion_article
        self.assertFalse(is_opinion_article(
            "Senate passes funding bill", "https://www.reviewjournal.com/news/2026/10/x/"))


class OpEdDomainOverrideTests(unittest.TestCase):
    def test_examiner(self):
        self.assertEqual(source_name_from_url("https://www.washingtonexaminer.com/opinion/x"),
                         "Washington Examiner")


if __name__ == "__main__":
    unittest.main()
