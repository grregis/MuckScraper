import unittest
from unittest.mock import patch

try:
    from news_fetcher import scheduler
    from aggregator.blueprints.admin import _shared as admin_shared
    from aggregator.blueprints.admin import config_crud as admin_config_crud
except ModuleNotFoundError as exc:
    scheduler = None
    admin_shared = None
    admin_config_crud = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class FakeRow:
    """Stand-in for a ScheduledFetch row, so these tests exercise the two
    accessors' mapping logic without needing a database."""

    def __init__(self, **kwargs):
        defaults = {
            "label": "Test",
            "description": None,
            "mode": "query",
            "newsapi_country": None,
            "newsapi_category": None,
            "newsapi_query": None,
            "gnews_query": None,
            "gnews_category": None,
        }
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(self, key, value)


def _patch_rows(module, target, rows):
    """Patch out the ScheduledFetch query chain both accessors use."""
    query = patch.object(module, target)
    mock = query.start()
    mock.query.filter_by.return_value.order_by.return_value.all.return_value = rows
    return query, mock


class ScheduledFetchAccessorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    def test_scheduler_maps_newsapi_columns_to_fetch_kwargs(self):
        # The DB columns are newsapi_* (a bare `query` attribute would shadow
        # Flask-SQLAlchemy's Model.query), but fetch_and_store_articles still
        # takes query/country/category -- this mapping is what bridges them.
        row = FakeRow(
            label="World News", mode="query",
            newsapi_query="international world global news",
            gnews_query="world global news", gnews_category="world",
        )
        patcher, _ = _patch_rows(scheduler, "ScheduledFetch", [row])
        self.addCleanup(patcher.stop)

        result = scheduler.get_scheduled_fetches()

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0],
            {
                "label": "World News",
                "mode": "query",
                "country": None,
                "category": None,
                "query": "international world global news",
                "gnews_query": "world global news",
                "gnews_category": "world",
            },
        )

    def test_scheduler_returns_plain_dicts_not_orm_instances(self):
        # The caller's except branch rolls the session back, which would expire
        # live instances mid-loop; detaching here is deliberate, not incidental.
        patcher, _ = _patch_rows(scheduler, "ScheduledFetch", [FakeRow()])
        self.addCleanup(patcher.stop)

        for entry in scheduler.get_scheduled_fetches():
            self.assertIsInstance(entry, dict)

    def test_presets_coerce_none_to_empty_string(self):
        # Every value goes straight into a hidden form input, so a None would
        # post the literal string "None" back to the manual fetch endpoint.
        row = FakeRow(label="Sports", mode="top", newsapi_country="us", newsapi_category="sports")
        patcher, _ = _patch_rows(admin_shared, "ScheduledFetch", [row])
        self.addCleanup(patcher.stop)

        preset = admin_shared.fetch_presets()[0]

        self.assertEqual(preset["query"], "")
        self.assertEqual(preset["gnews_query"], "")
        self.assertEqual(preset["description"], "")
        self.assertEqual(preset["country"], "us")
        self.assertNotIn(None, preset.values())

    def test_both_accessors_filter_to_active_rows(self):
        for module, target in ((scheduler, "ScheduledFetch"), (admin_shared, "ScheduledFetch")):
            with self.subTest(module=module.__name__):
                patcher, mock = _patch_rows(module, target, [])
                self.addCleanup(patcher.stop)

                if module is scheduler:
                    scheduler.get_scheduled_fetches()
                else:
                    admin_shared.fetch_presets()

                mock.query.filter_by.assert_called_once_with(is_active=True)


class ScheduledFetchFormValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    def _values(self, form):
        return admin_config_crud._scheduled_fetch_form_values(form)

    def test_query_mode_clears_top_headline_fields(self):
        values = self._values({
            "mode": "query", "newsapi_query": "some terms",
            "newsapi_country": "us", "newsapi_category": "business",
        })

        self.assertEqual(values["newsapi_query"], "some terms")
        self.assertIsNone(values["newsapi_country"])
        self.assertIsNone(values["newsapi_category"])

    def test_top_mode_clears_the_search_query(self):
        values = self._values({
            "mode": "top", "newsapi_query": "leftover",
            "newsapi_country": "us", "newsapi_category": "sports",
        })

        self.assertIsNone(values["newsapi_query"])
        self.assertEqual(values["newsapi_country"], "us")
        self.assertEqual(values["newsapi_category"], "sports")

    def test_unknown_mode_falls_back_to_query(self):
        values = self._values({"mode": "banana", "newsapi_query": "terms"})

        self.assertEqual(values["mode"], "query")

    def test_blank_fields_become_none_not_empty_string(self):
        values = self._values({"mode": "query", "newsapi_query": "  ", "gnews_query": ""})

        self.assertIsNone(values["newsapi_query"])
        self.assertIsNone(values["gnews_query"])


if __name__ == "__main__":
    unittest.main()
