"""RSS feed fallback (rss_fetcher.fetch_feed) and the Op/Ed section filter, offline."""
import unittest
from unittest import mock

import feedparser

from aggregator.article_signals import is_off_section_oped
from news_fetcher import rss_fetcher

PRIMARY = "https://feeds.washingtonpost.com/rss/opinions"
FALLBACK = "https://news.google.com/rss/search?q=site:washingtonpost.com/opinions+when:1d"


def parsed(status=200, entries=(), bozo=0):
    d = feedparser.FeedParserDict()
    d["status"], d["bozo"] = status, bozo
    d["feed"] = feedparser.FeedParserDict(title="The Washington Post")
    d["entries"] = [feedparser.FeedParserDict(e) for e in entries]
    return d


ENTRY = {"title": "In defense of zoning", "link": "https://www.washingtonpost.com/opinions/2026/10/18/zoning/"}


class FetchFeedFallbackTests(unittest.TestCase):
    def fetch(self, primary, fallback_url=FALLBACK):
        fetched = []

        def fake_parse(url):
            fetched.append(url)
            return primary if url == PRIMARY else parsed(entries=[ENTRY])

        with mock.patch.object(rss_fetcher, "_parse_feed", side_effect=fake_parse), \
             mock.patch.object(rss_fetcher.google_news, "is_google_news_feed", return_value=False):
            _, articles = rss_fetcher.fetch_feed(PRIMARY, fallback_url=fallback_url)
        return fetched, articles

    def test_http_error_uses_fallback(self):
        fetched, articles = self.fetch(parsed(status=502, bozo=1))
        self.assertEqual(fetched, [PRIMARY, FALLBACK])
        self.assertEqual(len(articles), 1)

    def test_404_uses_fallback(self):
        fetched, _ = self.fetch(parsed(status=404))
        self.assertEqual(fetched, [PRIMARY, FALLBACK])

    def test_unparseable_empty_feed_uses_fallback(self):
        fetched, _ = self.fetch(parsed(status=None, bozo=1))
        self.assertEqual(fetched, [PRIMARY, FALLBACK])

    def test_healthy_feed_does_not(self):
        fetched, articles = self.fetch(parsed(entries=[ENTRY]))
        self.assertEqual(fetched, [PRIMARY])
        self.assertEqual(len(articles), 1)

    def test_quiet_feed_does_not(self):
        # A feed that is up but has nothing new is not a failure.
        fetched, _ = self.fetch(parsed(entries=[]))
        self.assertEqual(fetched, [PRIMARY])

    def test_no_fallback_set(self):
        fetched, articles = self.fetch(parsed(status=502, bozo=1), fallback_url=None)
        self.assertEqual(fetched, [PRIMARY])
        self.assertEqual(articles, [])

    def test_exception_uses_fallback(self):
        calls = []

        def fake_parse(url):
            calls.append(url)
            if url == PRIMARY:
                raise OSError("connection reset")
            return parsed(entries=[ENTRY])

        with mock.patch.object(rss_fetcher, "_parse_feed", side_effect=fake_parse), \
             mock.patch.object(rss_fetcher.google_news, "is_google_news_feed", return_value=False):
            _, articles = rss_fetcher.fetch_feed(PRIMARY, fallback_url=FALLBACK)
        self.assertEqual(calls, [PRIMARY, FALLBACK])
        self.assertEqual(len(articles), 1)


class FeedTimeoutTests(unittest.TestCase):
    """_parse_feed against real local sockets (2026-11-06: the Washington
    Post's feed server accepted connections and sent nothing; the run hung)."""

    def serve(self, handler):
        import socket
        import threading
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        self.addCleanup(server.close)
        held = []
        self.addCleanup(lambda: [c.close() for c in held])

        def loop():
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                held.append(conn)
                handler(conn)

        threading.Thread(target=loop, daemon=True).start()
        return f"http://127.0.0.1:{server.getsockname()[1]}/feed"

    @staticmethod
    def reply(status, body, content_type="application/rss+xml"):
        def handler(conn):
            conn.recv(4096)
            conn.sendall((f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\n"
                          f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body)
        return handler

    RSS = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>Test Feed</title>'
           b'<item><title>In defense of zoning</title>'
           b'<link>https://www.washingtonpost.com/opinions/2026/10/18/zoning/</link></item>'
           b'</channel></rss>')

    def test_stalled_server_gives_up_and_uses_fallback(self):
        import time
        stalled = self.serve(lambda conn: None)          # accepts, never answers
        calls = []
        real = rss_fetcher._parse_feed

        def parse(url):
            calls.append(url)
            return real(url) if url == stalled else parsed(entries=[ENTRY])

        start = time.monotonic()
        with mock.patch.object(rss_fetcher, "FEED_TIMEOUT", (1, 1)), \
             mock.patch.object(rss_fetcher, "_parse_feed", side_effect=parse), \
             mock.patch.object(rss_fetcher.google_news, "is_google_news_feed", return_value=False):
            _, articles = rss_fetcher.fetch_feed(stalled, fallback_url=FALLBACK)
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(calls, [stalled, FALLBACK])
        self.assertEqual(len(articles), 1)

    def test_http_error_is_reported_as_status(self):
        url = self.serve(self.reply("502 Bad Gateway", b"<html>bad gateway</html>", "text/html"))
        feed = rss_fetcher._parse_feed(url)
        self.assertEqual(feed["status"], 502)
        self.assertTrue(rss_fetcher._feed_failed(feed))

    def test_healthy_feed_parses(self):
        url = self.serve(self.reply("200 OK", self.RSS))
        feed = rss_fetcher._parse_feed(url)
        self.assertEqual(feed["status"], 200)
        self.assertFalse(rss_fetcher._feed_failed(feed))
        self.assertEqual(feed.feed.title, "Test Feed")
        self.assertEqual([e.title for e in feed.entries], ["In defense of zoning"])

    def test_sends_feedparsers_user_agent(self):
        seen = []

        def handler(conn):
            seen.append(conn.recv(4096).decode(errors="replace"))
            self.reply("200 OK", self.RSS)(_Replay(conn))

        url = self.serve(handler)
        rss_fetcher._parse_feed(url)
        self.assertIn(f"User-Agent: {feedparser.USER_AGENT}", seen[0])


class _Replay:
    """Lets reply() run on a connection whose request was already read."""
    def __init__(self, conn):
        self.conn = conn

    def recv(self, n):
        return b""

    def sendall(self, data):
        self.conn.sendall(data)


class OffSectionOpEdTests(unittest.TestCase):
    def test_nyt_news_in_opinion_feed(self):
        # The two real cases (opinion_pieces 5089, 4792).
        self.assertTrue(is_off_section_oped(
            "https://www.nytimes.com/2026/10/16/us/elections/california-election-voters-trump-administration.html"))
        self.assertTrue(is_off_section_oped(
            "https://www.nytimes.com/2026/10/14/us/los-angeles-mayor-debate-takeaways.html"))

    def test_nyt_and_wapo_columns_kept(self):
        self.assertFalse(is_off_section_oped("https://www.nytimes.com/2026/10/17/opinion/trump-sanctions.html"))
        self.assertFalse(is_off_section_oped("https://www.washingtonpost.com/opinions/2026/10/18/zoning/"))
        self.assertTrue(is_off_section_oped("https://www.washingtonpost.com/world/2026/10/18/iran/"))

    def test_other_outlets_untouched(self):
        # Washington Times and National Review columns carry no /opinion/.
        self.assertFalse(is_off_section_oped("https://www.washingtontimes.com/news/2026/oct/17/george-soros-time-hes-outspent/"))
        self.assertFalse(is_off_section_oped("https://www.nationalreview.com/2026/10/congress-must-investigate/"))
        self.assertFalse(is_off_section_oped("https://www.theguardian.com/business/2026/oct/18/small-business-owners"))
        self.assertFalse(is_off_section_oped(None))


class MediaOpEdTests(unittest.TestCase):
    def test_video_and_audio_copies(self):
        from aggregator.article_signals import is_media_oped
        self.assertTrue(is_media_oped(
            "https://www.nytimes.com/video/opinion/100000011191541/this-is-nuts-an-openai-insider-explains-why-he-quit.html"))
        self.assertTrue(is_media_oped(
            "https://www.theguardian.com/politics/commentisfree/audio/2026/oct/17/austerity-is-inevitably-painful"))

    def test_columns_and_letters_kept(self):
        from aggregator.article_signals import is_media_oped
        self.assertFalse(is_media_oped("https://www.nytimes.com/2026/10/07/opinion/ezra-klein-podcast-david-robinson.html"))
        self.assertFalse(is_media_oped("https://www.nytimes.com/2026/10/13/opinion/letters/death-icu-hospital-home.html"))
        self.assertFalse(is_media_oped("https://www.latimes.com/opinion/letters-to-the-editor/story/2026-10-18/x"))
        self.assertFalse(is_media_oped(None))


class OpEdTitlePrefixTests(unittest.TestCase):
    def test_strips_only_a_leading_label(self):
        from news_fetcher.fetch_and_store_articles.ingestion import _OPINION_TITLE_PREFIX as p
        self.assertEqual(p.sub("", "Opinion | In defense of Kimberly Guilfoyle"), "In defense of Kimberly Guilfoyle")
        self.assertEqual(p.sub("", "OPINION: Carney's budget"), "Carney's budget")
        self.assertEqual(p.sub("", "Public opinion | shifts"), "Public opinion | shifts")
        self.assertEqual(p.sub("", "Opinionated chefs"), "Opinionated chefs")


if __name__ == "__main__":
    unittest.main()
