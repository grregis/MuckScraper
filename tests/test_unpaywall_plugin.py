"""Tests for the pluggable scraper fallback system and the unpaywall plugin."""

import unittest
from unittest import mock
from bs4 import BeautifulSoup

from news_fetcher import scraper_plugins
from news_fetcher import unpaywall
from news_fetcher.scraper import ScrapeResult, SCRAPE_STATUS_FALLBACK


class UnpaywallPluginTests(unittest.TestCase):
    def setUp(self):
        scraper_plugins.clear_fallback_plugins()

    def tearDown(self):
        scraper_plugins.clear_fallback_plugins()

    def test_clean_url_for_archives_strips_tracking_params(self):
        raw_url = "https://www.example.com/world/news?utm_source=twitter&utm_medium=social&article_id=456&ref=rss"
        cleaned = unpaywall.clean_url_for_archives(raw_url)
        self.assertIn("article_id=456", cleaned)
        self.assertNotIn("utm_source", cleaned)
        self.assertNotIn("utm_medium", cleaned)
        self.assertNotIn("ref=rss", cleaned)

    def test_make_wayback_raw_url_injects_id_flag(self):
        standard_url = "http://web.archive.org/web/20261105181658/https://example.com/article"
        raw_url = unpaywall.make_wayback_raw_url(standard_url)
        self.assertEqual(raw_url, "http://web.archive.org/web/20261105181658id_/https://example.com/article")

        # Idempotent if id_ already present
        self.assertEqual(unpaywall.make_wayback_raw_url(raw_url), raw_url)

    def test_extract_from_html_cascade_reads_structured_metadata_when_readability_sparse(self):
        import json
        payload = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "articleBody": "This is an in-depth opening paragraph of a major news event. " * 5 + "\n\n" +
                           "This is a second explanatory paragraph detailing the consequences. " * 5
        }
        sparse_html = f"<html><head><script type='application/ld+json'>{json.dumps(payload)}</script></head><body><p>Short snippet</p></body></html>"
        extracted = unpaywall.extract_from_html_cascade(sparse_html, "https://example.com/test")
        self.assertIsNotNone(extracted)
        self.assertIn("in-depth opening paragraph", extracted)

    def test_fetch_wayback_uses_raw_url(self):
        url = "https://example.com/paywalled"
        mock_api_resp = mock.Mock()
        mock_api_resp.status_code = 200
        mock_api_resp.json.return_value = {
            "archived_snapshots": {
                "closest": {
                    "available": True,
                    "url": "http://web.archive.org/web/20261105181658/https://example.com/paywalled"
                }
            }
        }

        mock_snap_resp = mock.Mock()
        mock_snap_resp.status_code = 200
        mock_snap_resp.text = """
        <html><body><article>
        <p>""" + ("Full recovered article content from Wayback Machine. " * 15) + """</p>
        </article></body></html>
        """

        def mock_get(req_url, *args, **kwargs):
            if "archive.org/wayback/available" in req_url:
                return mock_api_resp
            if "web.archive.org/web/" in req_url:
                # Must be requested with id_
                self.assertIn("20261105181658id_", req_url)
                return mock_snap_resp
            raise AssertionError(f"Unexpected GET {req_url}")

        with mock.patch("requests.get", side_effect=mock_get):
            content, method = unpaywall.fetch_wayback(url)
            self.assertIsNotNone(content)
            self.assertEqual(method, "wayback_snapshot")

    def test_archive_today_rejects_cloudflare_challenge(self):
        url = "https://example.com/paywalled"
        mock_redirect = mock.Mock()
        mock_redirect.status_code = 302
        mock_redirect.headers = {"Location": "/12345"}

        mock_challenge = mock.Mock()
        mock_challenge.status_code = 200
        mock_challenge.text = "<html><body><p>Please verify you are human to continue</p></body></html>"

        def mock_get(req_url, *args, **kwargs):
            if "/newest/" in req_url:
                return mock_redirect
            return mock_challenge

        with mock.patch("requests.get", side_effect=mock_get):
            content, method = unpaywall.fetch_archive_today(url)
            self.assertIsNone(content)
            self.assertIsNone(method)

    def test_scraper_plugin_system_executes_registered_fallbacks(self):
        url = "https://example.com/blocked-story"
        called = False

        def custom_plugin(target_url, initial_html=None):
            nonlocal called
            called = True
            return "<p>Recovered by custom plugin</p>", "custom_engine"

        scraper_plugins.register_fallback_plugin("test_plugin", custom_plugin)
        content, method = scraper_plugins.run_fallback_plugins(url)

        self.assertTrue(called)
        self.assertEqual(content, "<p>Recovered by custom plugin</p>")
        self.assertEqual(method, "test_plugin_custom_engine")

    def test_init_plugins_from_env_loads_unpaywall_when_flag_enabled(self):
        with mock.patch.dict("os.environ", {"ENABLE_UNPAYWALL_FALLBACK": "true"}):
            plugins = scraper_plugins.get_fallback_plugins()
            self.assertEqual(len(plugins), 1)
            self.assertEqual(plugins[0][0], "unpaywall")

    def test_init_plugins_from_env_loads_custom_spec(self):
        with mock.patch.dict("os.environ", {"SCRAPER_FALLBACK_PLUGINS": "news_fetcher.unpaywall:unpaywall_article"}):
            plugins = scraper_plugins.get_fallback_plugins()
            self.assertEqual(len(plugins), 1)
            self.assertEqual(plugins[0][0], "unpaywall")


if __name__ == "__main__":
    unittest.main()
