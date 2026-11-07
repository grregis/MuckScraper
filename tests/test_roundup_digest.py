import unittest

from aggregator.article_signals import is_roundup_article, low_value_article_reason


class NewsOfTheDayDigestTests(unittest.TestCase):
    # Real Toronto Sun titles (2026-10).
    DIGESTS = [
        "News of the day: Missing Whitby girl found safe in Mississauga, and more",
        "News of the day: Race for Mississauga mayor could go down to wire, and more",
        "News of the day: Should Canadians have to pay to watch Stanley Cup playoffs? And more",
    ]
    KEEP = [
        "Missing Whitby girl found safe in Mississauga after search",
        "The biggest news of the day: Fed cuts rates again",
        "HPE, IBD Stock Of The Day, Breaks Out On Hiked Guidance",
        "Wall Street's merger frenzy takes a breather: Chart of the Day",
    ]

    def test_digests_are_roundups(self):
        for title in self.DIGESTS:
            self.assertTrue(is_roundup_article(title, "https://torontosun.com/news/x"), title)
            self.assertEqual(low_value_article_reason(title, "https://torontosun.com/news/x"), "roundup")

    def test_news_titles_kept(self):
        for title in self.KEEP:
            self.assertFalse(is_roundup_article(title, "https://example.com/news/x"), title)


if __name__ == "__main__":
    unittest.main()
