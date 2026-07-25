"""Tests for the Ollama primary/fallback host selection in llm_client.

These exercise the health-cache behavior added for upstream issue #7:
- with no fallback configured, behavior is identical to the old single-host code
- a down primary is skipped after the first probe and the fallback serves
- once the primary comes back, an interval-gated re-probe recovers to it
"""

import unittest
from unittest import mock

from news_fetcher import llm_client


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class OllamaFallbackTests(unittest.TestCase):
    PRIMARY = "http://primary:11434"
    FALLBACK = "http://fallback:11434"

    def setUp(self):
        # Reset the module-level host-health state before each test so cases
        # don't leak "active host" decisions into one another.
        llm_client._ollama_state["active"] = None
        llm_client._ollama_state["next_primary_probe"] = 0.0
        self._patchers = [
            mock.patch.object(llm_client, "OLLAMA_HOST", self.PRIMARY),
            mock.patch.object(llm_client, "OLLAMA_FALLBACK_HOST", self.FALLBACK),
            mock.patch.object(llm_client, "OLLAMA_MODEL", "test-model"),
            mock.patch.object(llm_client, "OLLAMA_PRIMARY_RECHECK_SECONDS", 60),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    # -- no fallback configured: unchanged single-host behavior --------------

    def test_no_fallback_uses_only_primary(self):
        with mock.patch.object(llm_client, "OLLAMA_FALLBACK_HOST", ""):
            self.assertEqual(llm_client._ollama_host_candidates(), [self.PRIMARY])

    def test_no_fallback_generate_hits_primary_only(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            return _FakeResponse(json_data={"response": "hi"})

        with mock.patch.object(llm_client, "OLLAMA_FALLBACK_HOST", ""), \
                mock.patch.object(llm_client.requests, "post", side_effect=fake_post):
            result = llm_client._generate_text_ollama("prompt", timeout=30)

        self.assertEqual(result, "hi")
        self.assertEqual(calls, [f"{self.PRIMARY}/api/generate"])

    # -- primary healthy -----------------------------------------------------

    def test_primary_up_serves_from_primary(self):
        posts = []

        def fake_get(url, **kwargs):  # the pre-call probe of the primary
            return _FakeResponse(status_code=200)

        def fake_post(url, **kwargs):
            posts.append(url)
            return _FakeResponse(json_data={"response": "ok"})

        with mock.patch.object(llm_client.requests, "get", side_effect=fake_get), \
                mock.patch.object(llm_client.requests, "post", side_effect=fake_post):
            result = llm_client._generate_text_ollama("p", timeout=30)

        self.assertEqual(result, "ok")
        self.assertEqual(posts, [f"{self.PRIMARY}/api/generate"])
        self.assertEqual(llm_client._ollama_state["active"], "primary")

    # -- primary down: probe fails, fallback serves --------------------------

    def test_primary_probe_fails_uses_fallback(self):
        posts = []

        def fake_get(url, **kwargs):  # primary probe -> unreachable
            raise ConnectionError("primary asleep")

        def fake_post(url, **kwargs):
            posts.append(url)
            return _FakeResponse(json_data={"response": "from-fallback"})

        with mock.patch.object(llm_client.requests, "get", side_effect=fake_get), \
                mock.patch.object(llm_client.requests, "post", side_effect=fake_post):
            result = llm_client._generate_text_ollama("p", timeout=30)

        self.assertEqual(result, "from-fallback")
        # The primary was never POSTed to -- the cheap probe steered us away.
        self.assertEqual(posts, [f"{self.FALLBACK}/api/generate"])
        self.assertEqual(llm_client._ollama_state["active"], "fallback")

    def test_parked_on_fallback_skips_primary_probe_until_recheck(self):
        """Once parked on the fallback, calls within the recheck window must not
        probe the primary again (that's the whole point -- no repeated timeouts)."""
        llm_client._ollama_state["active"] = "fallback"
        # Far in the future so the recheck window has NOT elapsed.
        import time
        llm_client._ollama_state["next_primary_probe"] = time.monotonic() + 10_000

        get_calls = []

        def fake_get(url, **kwargs):
            get_calls.append(url)
            return _FakeResponse(status_code=200)

        with mock.patch.object(llm_client.requests, "get", side_effect=fake_get):
            candidates = llm_client._ollama_host_candidates()

        self.assertEqual(candidates, [self.FALLBACK, self.PRIMARY])
        self.assertEqual(get_calls, [])  # no probe issued

    def test_recheck_recovers_to_primary_when_back(self):
        """After the recheck window elapses, a now-reachable primary is picked
        back up automatically (issue #7's auto-recovery)."""
        llm_client._ollama_state["active"] = "fallback"
        llm_client._ollama_state["next_primary_probe"] = 0.0  # window already elapsed

        def fake_get(url, **kwargs):  # primary now answers
            return _FakeResponse(status_code=200)

        with mock.patch.object(llm_client.requests, "get", side_effect=fake_get):
            candidates = llm_client._ollama_host_candidates()

        self.assertEqual(candidates, [self.PRIMARY, self.FALLBACK])
        self.assertEqual(llm_client._ollama_state["active"], "primary")

    # -- request-time failure falls through to the other host ----------------

    def test_primary_generate_error_falls_through_to_fallback(self):
        """If the primary passes its probe but the actual generate call fails,
        the request still completes on the fallback and state parks there."""
        llm_client._ollama_state["active"] = "primary"
        llm_client._ollama_state["next_primary_probe"] = 1e18  # don't re-probe

        posts = []

        def fake_post(url, **kwargs):
            posts.append(url)
            if url.startswith(self.PRIMARY):
                raise ConnectionError("primary died mid-request")
            return _FakeResponse(json_data={"response": "rescued"})

        with mock.patch.object(llm_client.requests, "post", side_effect=fake_post):
            result = llm_client._generate_text_ollama("p", timeout=30)

        self.assertEqual(result, "rescued")
        self.assertEqual(
            posts,
            [f"{self.PRIMARY}/api/generate", f"{self.FALLBACK}/api/generate"],
        )
        self.assertEqual(llm_client._ollama_state["active"], "fallback")

    # -- status surface ------------------------------------------------------

    def test_status_reports_online_when_only_fallback_up(self):
        def fake_get(url, **kwargs):
            if url.startswith(self.PRIMARY):
                raise ConnectionError("down")
            return _FakeResponse(status_code=200)

        with mock.patch.object(llm_client.requests, "get", side_effect=fake_get):
            self.assertTrue(llm_client._check_ollama_status())
            info = llm_client.ollama_host_status()

        self.assertEqual(info, {"online": True, "host": self.FALLBACK, "role": "fallback"})

    def test_status_prefers_primary_role_when_both_up(self):
        with mock.patch.object(llm_client.requests, "get",
                               side_effect=lambda url, **k: _FakeResponse(status_code=200)):
            info = llm_client.ollama_host_status()
        self.assertEqual(info["role"], "primary")
        self.assertEqual(info["host"], self.PRIMARY)

    def test_status_offline_when_neither_reachable(self):
        with mock.patch.object(llm_client.requests, "get",
                               side_effect=ConnectionError("nope")):
            self.assertFalse(llm_client._check_ollama_status())
            info = llm_client.ollama_host_status()
        self.assertEqual(info, {"online": False, "host": None, "role": None})

    def test_llm_status_detail_includes_provider_for_ollama(self):
        with mock.patch.object(llm_client, "LLM_PROVIDER", "ollama"), \
                mock.patch.object(llm_client.requests, "get",
                                  side_effect=lambda url, **k: _FakeResponse(status_code=200)):
            detail = llm_client.llm_status_detail()
        self.assertEqual(detail["provider"], "ollama")
        self.assertEqual(detail["role"], "primary")


if __name__ == "__main__":
    unittest.main()
