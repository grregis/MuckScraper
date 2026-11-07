import unittest
from unittest.mock import patch

try:
    from news_fetcher.fetch_and_store_articles import ingestion as fsa
except ModuleNotFoundError as exc:
    fsa = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class FakeBlock:
    def __init__(self, kind, pattern):
        self.kind = kind
        self.pattern = pattern


def _patch_blocks(rows):
    patcher = patch.object(fsa, "IngestionBlock")
    mock = patcher.start()
    mock.query.filter_by.return_value.all.return_value = rows
    return patcher, mock


class IngestionBlockAccessorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    def _blocks(self, rows):
        patcher, _ = _patch_blocks(rows)
        self.addCleanup(patcher.stop)
        return fsa.get_ingestion_blocks()

    def test_splits_rows_by_kind(self):
        sources, titles = self._blocks([
            FakeBlock("source", "nfl.com"),
            FakeBlock("title_keyword", "game recap"),
            FakeBlock("source", "prnewswire.com"),
        ])

        self.assertEqual(sources, ["nfl.com", "prnewswire.com"])
        self.assertEqual(titles, ["game recap"])

    def test_lowercases_patterns_at_load(self):
        # The per-article checks compare against an already-lowered url/title
        # and do NOT lower the pattern, so a mixed-case row would never fire.
        sources, titles = self._blocks([
            FakeBlock("source", "NFL.com"),
            FakeBlock("title_keyword", "Nintendo"),
        ])

        self.assertEqual(sources, ["nfl.com"])
        self.assertEqual(titles, ["nintendo"])

    def test_strips_whitespace_and_drops_blank_patterns(self):
        sources, titles = self._blocks([
            FakeBlock("source", "  nfl.com  "),
            FakeBlock("source", "   "),
            FakeBlock("source", None),
        ])

        self.assertEqual(sources, ["nfl.com"])
        self.assertEqual(titles, [])

    def test_ignores_unknown_kinds(self):
        sources, titles = self._blocks([FakeBlock("something_else", "nfl.com")])

        self.assertEqual(sources, [])
        self.assertEqual(titles, [])

    def test_only_active_rows_are_loaded(self):
        patcher, mock = _patch_blocks([])
        self.addCleanup(patcher.stop)

        fsa.get_ingestion_blocks()

        mock.query.filter_by.assert_called_once_with(is_active=True)


class IngestionBlockMatchingTests(unittest.TestCase):
    """The substring semantics the stored patterns rely on."""

    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    @staticmethod
    def _source_blocked(url, patterns):
        return any(p in url.lower() for p in patterns)

    @staticmethod
    def _title_blocked(title, patterns):
        return any(p in title.lower() for p in patterns)

    def test_league_domain_covers_subdomains(self):
        # This is why the patterns are substrings and not exact hostnames.
        self.assertTrue(self._source_blocked(
            "https://www-frontend.pocket.nfl.com/news/story", ["nfl.com"]))
        self.assertTrue(self._source_blocked("https://fever.wnba.com/x", ["nba.com"]))

    def test_source_match_is_case_insensitive(self):
        self.assertTrue(self._source_blocked("https://WWW.NFL.COM/news", ["nfl.com"]))

    def test_title_keyword_matches_regardless_of_case(self):
        self.assertTrue(self._title_blocked("New Nintendo Switch revealed", ["nintendo"]))

    def test_unrelated_article_is_not_blocked(self):
        patterns = ["nfl.com", "prnewswire.com"]
        self.assertFalse(self._source_blocked("https://apnews.com/article/senate", patterns))


if __name__ == "__main__":
    unittest.main()
