import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

try:
    from news_fetcher import scraper
except ModuleNotFoundError as exc:
    scraper = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class ScraperRetryPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    def setUp(self):
        self.cache = {"domains": {}, "urls": {}}

    def patch_cache_io(self):
        def fake_load():
            return deepcopy(self.cache)

        def fake_save(cache):
            self.cache = deepcopy(cache)

        return patch.object(scraper, "_load_retry_cache", side_effect=fake_load), patch.object(
            scraper, "_save_retry_cache", side_effect=fake_save
        )

    def test_failed_scrape_creates_url_retry_deferral(self):
        now = datetime(2026, 5, 12, 9, 0, 0)
        result = scraper.ScrapeResult(
            content=None,
            status=scraper.SCRAPE_STATUS_FAILED,
            method="bs4",
            failure_reason="http_500",
            http_status=500,
        )

        load_patch, save_patch = self.patch_cache_io()
        with load_patch, save_patch:
            scraper.update_retry_cache("https://example.com/story?id=1", result, now=now)
            scope, entry = scraper.get_retry_deferral("https://example.com/story?id=2", now=now + timedelta(minutes=5))

        self.assertEqual(scope, "url")
        self.assertEqual(entry["status"], scraper.SCRAPE_STATUS_FAILED)
        self.assertEqual(entry["failure_count"], 1)

    def test_repeated_domain_failures_create_domain_retry_deferral(self):
        now = datetime(2026, 5, 12, 9, 0, 0)
        result = scraper.ScrapeResult(
            content=None,
            status=scraper.SCRAPE_STATUS_FAILED,
            method="bs4",
            failure_reason="extraction_failed",
            http_status=None,
        )

        load_patch, save_patch = self.patch_cache_io()
        with load_patch, save_patch:
            for idx in range(3):
                scraper.update_retry_cache(f"https://example.com/story-{idx}", result, now=now + timedelta(minutes=idx))

        self.assertIn("example.com", self.cache["domains"])
        self.assertEqual(self.cache["domains"]["example.com"]["failure_count"], 3)

    def test_success_clears_existing_retry_deferral(self):
        now = datetime(2026, 5, 12, 9, 0, 0)
        failure = scraper.ScrapeResult(
            content=None,
            status=scraper.SCRAPE_STATUS_FAILED,
            method="bs4",
            failure_reason="http_500",
            http_status=500,
        )
        success = scraper.ScrapeResult(
            content="<p>ok</p>",
            status=scraper.SCRAPE_STATUS_SUCCESS,
            method="bs4",
        )

        load_patch, save_patch = self.patch_cache_io()
        with load_patch, save_patch:
            scraper.update_retry_cache("https://example.com/story", failure, now=now)
            scraper.update_retry_cache("https://example.com/story", success, now=now + timedelta(minutes=30))
            scope, entry = scraper.get_retry_deferral("https://example.com/story", now=now + timedelta(minutes=31))

        self.assertIsNone(scope)
        self.assertIsNone(entry)

    def test_fallback_clears_only_url_retry_deferral(self):
        now = datetime(2026, 5, 12, 9, 0, 0)
        failure = scraper.ScrapeResult(
            content=None,
            status=scraper.SCRAPE_STATUS_FAILED,
            method="bs4",
            failure_reason="http_403",
            http_status=403,
        )
        fallback = scraper.ScrapeResult(
            content="<p>summary</p>",
            status=scraper.SCRAPE_STATUS_FALLBACK,
            method="feed_description",
        )

        load_patch, save_patch = self.patch_cache_io()
        with load_patch, save_patch:
            scraper.update_retry_cache("https://example.com/story", failure, now=now)
            scraper.update_retry_cache("https://example.com/story", fallback, now=now + timedelta(minutes=30))

        self.assertNotIn("https://example.com/story", self.cache["urls"])
        self.assertIn("example.com", self.cache["domains"])

    def test_should_auto_rescrape_article_skips_blocked_and_healthy_content(self):
        blocked = SimpleNamespace(scrape_status="blocked", content=None)
        healthy = SimpleNamespace(scrape_status="success", content="<p>" + ("word " * 200) + "</p>")
        weak_fallback = SimpleNamespace(scrape_status="fallback", content="<p>short summary</p>")
        pending = SimpleNamespace(scrape_status="pending", content="")

        self.assertFalse(scraper.should_auto_rescrape_article(blocked))
        self.assertFalse(scraper.should_auto_rescrape_article(healthy))
        self.assertTrue(scraper.should_auto_rescrape_article(weak_fallback))
        self.assertTrue(scraper.should_auto_rescrape_article(pending))


if __name__ == "__main__":
    unittest.main()
