"""Google News link resolution (news_fetcher/google_news.py), offline."""
import json
import unittest

from news_fetcher import google_news as gn

LINK = "https://news.google.com/rss/articles/CBMiekFVX3lxTE9rR3JkMU9N?oc=5"


class FakeResponse:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class FakeSession:
    def __init__(self, page, batch):
        self.page, self.batch, self.calls = page, batch, 0

    def get(self, *a, **k):
        self.calls += 1
        return self.page

    def post(self, *a, **k):
        self.calls += 1
        return self.batch


def batch_answer(url):
    inner = json.dumps(["garturlres", url, 1])
    return ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"], ["di", 10], ["af.httprm", 9, "x", 1]])


GOOD_PAGE = FakeResponse(200, '<c-wiz><div jscontroller="x" data-n-a-sg="SIG123" data-n-a-ts="1791400000"></div>')


class GoogleNewsTests(unittest.TestCase):
    def setUp(self):
        gn._RESOLVED_CACHE.clear()

    def test_article_id(self):
        self.assertEqual(gn.article_id(LINK), "CBMiekFVX3lxTE9rR3JkMU9N")
        self.assertIsNone(gn.article_id("https://www.cnn.com/2026/10/16/politics/x"))

    def test_strip_source_suffix_only_exact(self):
        self.assertEqual(gn.strip_source_suffix("Pope has surgery - CNN", "CNN"), "Pope has surgery")
        self.assertEqual(gn.strip_source_suffix("Biden - CNN interview", "CNN"), "Biden - CNN interview")
        self.assertEqual(gn.strip_source_suffix("A - B - AP News", "AP News"), "A - B")

    def test_strip_source_suffix_alias(self):
        same = lambda a, b: {a, b} <= {"WSJ", "The Wall Street Journal"}
        self.assertEqual(gn.strip_source_suffix("KFC to ditch Pepsi - WSJ", "The Wall Street Journal", same), "KFC to ditch Pepsi")
        # a " - " segment that isn't the outlet stays
        self.assertEqual(gn.strip_source_suffix("Live updates - Day 3", "The Wall Street Journal", same), "Live updates - Day 3")
        self.assertEqual(gn.strip_source_suffix("No matcher - WSJ", "The Wall Street Journal"), "No matcher - WSJ")

    def test_parse_batch_response(self):
        self.assertEqual(gn.parse_batch_response(batch_answer("https://apnews.com/article/x")), "https://apnews.com/article/x")
        self.assertIsNone(gn.parse_batch_response(batch_answer("https://news.google.com/x")))
        self.assertIsNone(gn.parse_batch_response("garbage"))

    def test_resolve_replaces_url(self):
        s = FakeSession(GOOD_PAGE, FakeResponse(200, batch_answer("https://www.cnn.com/a")))
        out, stats = gn.resolve_entries([{"title": "T", "source_name": "CNN", "url": LINK}], session=s, sleep=lambda _: None)
        self.assertEqual(out[0]["url"], "https://www.cnn.com/a")
        self.assertEqual(stats["resolved"], 1)

    def test_unresolved_entry_is_dropped_never_kept_with_google_link(self):
        s = FakeSession(FakeResponse(429, ""), FakeResponse(429, ""))
        out, stats = gn.resolve_entries([{"title": "T", "source_name": "CNN", "url": LINK}], session=s, sleep=lambda _: None)
        self.assertEqual(out, [])
        self.assertEqual(stats["failed"], 1)

    def test_stops_after_consecutive_failures(self):
        s = FakeSession(FakeResponse(503, ""), FakeResponse(503, ""))
        arts = [{"title": f"T{i}", "source_name": "CNN", "url": LINK + str(i)} for i in range(10)]
        out, stats = gn.resolve_entries(arts, session=s, sleep=lambda _: None)
        self.assertTrue(stats["stopped_early"])
        self.assertEqual(s.calls, gn.MAX_CONSECUTIVE_FAILURES)  # one GET each, no POST
        self.assertEqual(stats["failed"], 10)

    def test_old_entry_dropped_without_lookup(self):
        from datetime import datetime
        s = FakeSession("", "")
        now = datetime(2026, 10, 18, 12, 0)
        arts = [{"title": "Forget EVs", "source_name": "The Economist", "url": LINK,
                 "published_at": datetime(2025, 10, 9)}]
        out, stats = gn.resolve_entries(arts, session=s, sleep=lambda _: None, now=now)
        self.assertEqual((out, stats["too_old"], s.calls), ([], 1, 0))

    def test_already_stored_skips_lookup(self):
        s = FakeSession(GOOD_PAGE, FakeResponse(200, batch_answer("https://www.cnn.com/a")))
        out, stats = gn.resolve_entries([{"title": "T", "source_name": "CNN", "url": LINK}],
                                        already_stored=lambda t, src: True, session=s, sleep=lambda _: None)
        self.assertEqual((out, s.calls, stats["already_stored"]), ([], 0, 1))

    def test_cache_avoids_second_lookup(self):
        s = FakeSession(GOOD_PAGE, FakeResponse(200, batch_answer("https://www.cnn.com/a")))
        art = [{"title": "T", "source_name": "CNN", "url": LINK}]
        gn.resolve_entries(art, session=s, sleep=lambda _: None)
        calls = s.calls
        out, _ = gn.resolve_entries(art, session=s, sleep=lambda _: None)
        self.assertEqual((s.calls, out[0]["url"]), (calls, "https://www.cnn.com/a"))


if __name__ == "__main__":
    unittest.main()
