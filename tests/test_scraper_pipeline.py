import unittest
from unittest.mock import Mock
from unittest.mock import patch

import requests

try:
    from news_fetcher import scraper
except ModuleNotFoundError as exc:
    scraper = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class ScraperPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    def test_blocked_domain_returns_blocked_result(self):
        with patch.object(scraper, "is_domain_blocked", return_value=True):
            result = scraper.scrape_article("https://example.com/story")

        self.assertEqual(result.status, scraper.SCRAPE_STATUS_BLOCKED)
        self.assertEqual(result.method, "blocklist")
        self.assertIsNone(result.content)


    def test_bad_scrape_records_one_blocklist_hit_per_article_across_variants(self):
        # fableaudit.md follow-up N1: a wall page that renders the same under
        # every URL variant used to record one blocklist hit per variant --
        # a single Fox article produced hits 1-4 in 334 ms on 2026-09-13 and
        # cleared the 3-hit threshold on its own. One article is one hit.
        wall_html = "<html><body><p>Please verify you are human. captcha required to continue.</p></body></html>"

        class FakeResponse:
            status_code = 200
            text = wall_html

            def raise_for_status(self):
                return None

        wall_text = "<p>Please verify you are human. captcha required to continue.</p>"
        variants = [
            "https://example.com/story?output=amp",
            "https://example.com/story?amp=1",
            "https://example.com/story/amp",
        ]

        with patch.object(scraper, "is_domain_blocked", return_value=False), \
             patch.object(scraper, "should_skip", return_value=False), \
             patch.object(scraper, "_fetch_html", return_value=FakeResponse()), \
             patch.object(scraper, "extract_with_readability", return_value=wall_text), \
             patch.object(scraper, "extract_structured_metadata", return_value=None), \
             patch.object(scraper, "build_variant_urls", return_value=variants), \
             patch.object(scraper, "add_to_blocklist") as add_hit:
            result = scraper.scrape_article("https://example.com/story")

        self.assertEqual(add_hit.call_count, 1, "base URL plus three variants must count as one hit")
        self.assertIsNone(result.content)
        self.assertNotEqual(result.status, scraper.SCRAPE_STATUS_SUCCESS)

    def test_metadata_fallback_is_used_when_primary_extraction_fails(self):
        html = """
        <html>
          <head>
            <meta property="og:description" content="This is a long fallback description that should be long enough to survive the metadata fallback path and produce useful article text for downstream processing." />
          </head>
          <body></body>
        </html>
        """

        class FakeResponse:
            status_code = 200
            text = html

            def raise_for_status(self):
                return None

        with patch.object(scraper, "is_domain_blocked", return_value=False), \
             patch.object(scraper, "should_skip", return_value=False), \
             patch.object(scraper, "_fetch_html", return_value=FakeResponse()), \
             patch.object(scraper, "extract_with_readability", return_value=None), \
             patch.object(scraper, "extract_article_html_bs4", return_value=None), \
             patch.object(scraper, "build_variant_urls", return_value=[]):
            result = scraper.scrape_article("https://example.com/story")

        self.assertEqual(result.status, scraper.SCRAPE_STATUS_FALLBACK)
        self.assertEqual(result.method, "bs4_metadata")
        self.assertIn("fallback description", result.content)

    def test_base_page_byline_is_reported(self):
        html = """<html><head><script type="application/ld+json">
        {"@type": "NewsArticle", "author": {"@type": "Person", "name": "Jane Doe"}}
        </script></head><body><p>text</p></body></html>"""

        class FakeResponse:
            status_code = 200
            text = html

            def raise_for_status(self):
                return None

        with patch.object(scraper, "is_domain_blocked", return_value=False), \
             patch.object(scraper, "should_skip", return_value=False), \
             patch.object(scraper, "get_retry_deferral", return_value=(None, None)), \
             patch.object(scraper, "update_retry_cache"), \
             patch.object(scraper, "_fetch_html", return_value=FakeResponse()), \
             patch.object(scraper, "extract_with_readability", return_value="<p>" + "word " * 100 + "</p>"):
            result = scraper.scrape_article("https://example.com/story")

        self.assertEqual(result.method, "bs4")
        self.assertEqual(result.author, "Jane Doe")

    def test_feed_description_fallback_is_used_last(self):
        class FakeResponse:
            status_code = 200
            text = "<html><body><p>tiny</p></body></html>"

            def raise_for_status(self):
                return None

        fallback = "Feed summary text that is long enough to be preserved as the final content fallback when the scrape itself does not produce usable article body text."

        with patch.object(scraper, "is_domain_blocked", return_value=False), \
             patch.object(scraper, "should_skip", return_value=False), \
             patch.object(scraper, "_fetch_html", return_value=FakeResponse()), \
             patch.object(scraper, "extract_with_readability", return_value=None), \
             patch.object(scraper, "extract_structured_metadata", return_value=None), \
             patch.object(scraper, "extract_article_html_bs4", return_value=None), \
             patch.object(scraper, "build_variant_urls", return_value=[]):
            result = scraper.scrape_article("https://example.com/story", fallback_content=fallback)

        self.assertEqual(result.status, scraper.SCRAPE_STATUS_FALLBACK)
        self.assertEqual(result.method, "feed_description")
        self.assertIn("Feed summary text", result.content)

    def test_403_skips_variant_attempts_and_uses_feed_fallback(self):
        response = Mock()
        response.status_code = 403
        error = requests.HTTPError(response=response)

        with patch.object(scraper, "is_domain_blocked", return_value=False), \
             patch.object(scraper, "should_skip", return_value=False), \
             patch.object(scraper, "_fetch_html", side_effect=error), \
             patch.object(scraper, "build_variant_urls") as build_variant_urls:
            result = scraper.scrape_article(
                "https://example.com/story",
                fallback_content="Fallback text that is long enough to be retained after a blocked scrape attempt.",
            )

        build_variant_urls.assert_not_called()
        self.assertEqual(result.status, scraper.SCRAPE_STATUS_FALLBACK)
        self.assertEqual(result.method, "feed_description")

    def test_thehill_403_allows_variant_attempts(self):
        response = Mock()
        response.status_code = 403
        error = requests.HTTPError(response=response)

        with patch.object(scraper, "is_domain_blocked", return_value=False), \
             patch.object(scraper, "should_skip", return_value=False), \
             patch.object(scraper, "_fetch_html", side_effect=error), \
             patch.object(scraper, "build_variant_urls", return_value=["https://thehill.com/story/amp/"]) as build_variant_urls:
            result = scraper.scrape_article(
                "https://thehill.com/story",
                fallback_content="Fallback text that is long enough to be retained after a blocked scrape attempt.",
            )

        build_variant_urls.assert_called_once()
        self.assertEqual(result.status, scraper.SCRAPE_STATUS_FALLBACK)
        self.assertEqual(result.method, "feed_description")

    def test_build_variant_urls_prioritizes_thehill_amp_path(self):
        variants = scraper.build_variant_urls("https://thehill.com/homenews/example-story/")

        self.assertTrue(variants)
        self.assertEqual(variants[0], "https://thehill.com/homenews/example-story/amp/")


    def test_never_poses_as_a_crawler(self):
        # 2026-10-21: no Googlebot user agent, no headless browser for
        # paywalled sites. Every fetch goes out with the ordinary browser UA.
        seen = []

        def fake_fetch(url, headers=None, timeout=10):
            seen.append(headers)
            raise requests.HTTPError(response=Mock(status_code=403))

        with patch.object(scraper, "is_domain_blocked", return_value=False), \
             patch.object(scraper, "should_skip", return_value=False), \
             patch.object(scraper, "get_retry_deferral", return_value=(None, None)), \
             patch.object(scraper, "update_retry_cache"), \
             patch.object(scraper, "_fetch_html", side_effect=fake_fetch):
            scraper.scrape_article("https://www.businessinsider.com/some-story-2026-10")
            scraper.scrape_article("https://www.nytimes.com/2026/10/21/us/story.html")

        self.assertTrue(seen)
        for headers in seen:
            agent = (headers or scraper.HEADERS_DEFAULT)["User-Agent"].lower()
            self.assertNotIn("googlebot", agent)
            self.assertNotIn("bot", agent)
        self.assertFalse(hasattr(scraper, "GOOGLEBOT_DOMAINS"))
        self.assertFalse(hasattr(scraper, "extract_article_html_playwright"))


if __name__ == "__main__":
    unittest.main()
