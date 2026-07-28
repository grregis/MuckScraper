"""Tests for the GNews locale fix (GNews-Config-Bug).

`fetch_gnews` previously hardcoded `country="us"` + `lang="en"`, so the DE
downstream pulled US/EN news despite `fetch_country="de"` in seed_topics.
Now country/lang default to GNEWS_COUNTRY/GNEWS_LANG env (de/de), with
explicit args overriding. The DB layer is mocked so no Postgres/psycopg2
is needed for these unit tests.
"""

import os
import unittest
from unittest import mock

# fsa creates the Flask app at import time (module-level `app = create_app()`),
# which requires SECRET_KEY. Set test defaults before importing so the import
# succeeds without loading the real .env.
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from news_fetcher import fetch_and_store_articles as fsa


def _mock_response(articles=None):
    resp = mock.Mock()
    resp.json.return_value = {"articles": articles or []}
    resp.raise_for_status.return_value = None
    return resp


class GNewsLocaleTest(unittest.TestCase):
    def _run_gnews(self, **kwargs):
        with mock.patch.object(
                    fsa, "store_articles",
                    return_value={"input_articles": 0, "stored": 0},
                ), \
                mock.patch.object(fsa, "db"), \
                mock.patch.object(
                    fsa.requests, "get", return_value=_mock_response()
                ) as mock_get:
            fsa.fetch_gnews("DE Inland", **kwargs)
        return mock_get.call_args

    def test_category_fetch_uses_de_defaults(self):
        env = {"GNEWS_API_KEY": "test", "GNEWS_COUNTRY": "de", "GNEWS_LANG": "de"}
        with mock.patch.dict(os.environ, env):
            _, kwargs = self._run_gnews(category="nation")
        params = kwargs["params"]
        self.assertEqual(params["country"], "de")
        self.assertEqual(params["lang"], "de")

    def test_explicit_country_overrides_env(self):
        env = {"GNEWS_API_KEY": "test", "GNEWS_COUNTRY": "de", "GNEWS_LANG": "de"}
        with mock.patch.dict(os.environ, env):
            _, kwargs = self._run_gnews(category="world", country="us", lang="en")
        params = kwargs["params"]
        self.assertEqual(params["country"], "us")
        self.assertEqual(params["lang"], "en")

    def test_query_fetch_uses_de_lang_no_country(self):
        env = {"GNEWS_API_KEY": "test", "GNEWS_COUNTRY": "de", "GNEWS_LANG": "de"}
        with mock.patch.dict(os.environ, env):
            _, kwargs = self._run_gnews(query="Bundestag")
        params = kwargs["params"]
        self.assertEqual(params["lang"], "de")
        # query-branch has no country param
        self.assertNotIn("country", params)

    def test_env_unset_falls_back_to_de(self):
        # GNEWS_COUNTRY/GNEWS_LANG absent from env -> "de"/"de" default.
        with mock.patch.dict(os.environ, {"GNEWS_API_KEY": "test"}):
            os.environ.pop("GNEWS_COUNTRY", None)
            os.environ.pop("GNEWS_LANG", None)
            _, kwargs = self._run_gnews(category="nation")
        params = kwargs["params"]
        self.assertEqual(params["country"], "de")
        self.assertEqual(params["lang"], "de")


if __name__ == "__main__":
    unittest.main()