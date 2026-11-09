"""Tests for the shared OpenAI-compatible provider path in llm_client.

Phase 1 of upstream issue #8: Groq and OpenRouter are two configs over one
implementation, so any endpoint speaking `chat/completions` works by pointing
its *_HOST at it. These check that generalizing the old Groq-only function
changed nothing about how Groq behaves, and that OpenRouter rides the same path.
"""

import unittest
from unittest import mock

from news_fetcher import llm_client


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def _completion(text):
    return _FakeResponse(json_data={"choices": [{"message": {"content": text}}]})


class OpenAICompatibleRequestTests(unittest.TestCase):
    def setUp(self):
        self._patchers = [
            mock.patch.object(llm_client, "GROQ_API_KEY", "groq-key"),
            mock.patch.object(llm_client, "GROQ_MODEL", "groq-model"),
            mock.patch.object(llm_client, "GROQ_HOST", "https://api.groq.com/openai/v1"),
            mock.patch.object(llm_client, "OPENROUTER_API_KEY", "or-key"),
            mock.patch.object(llm_client, "OPENROUTER_MODEL", "or-model"),
            mock.patch.object(llm_client, "OPENROUTER_HOST", "https://openrouter.ai/api/v1"),
            mock.patch.object(llm_client, "OPENROUTER_SITE_URL", ""),
            mock.patch.object(llm_client, "OPENROUTER_APP_NAME", ""),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patchers])

    def test_groq_still_hits_its_original_endpoint(self):
        # The whole point of the refactor is that Groq's behavior is unchanged.
        with mock.patch.object(llm_client.requests, "post", return_value=_completion("hi")) as post:
            result = llm_client._generate_text_groq("prompt", 30)

        self.assertEqual(result, "hi")
        url, = post.call_args[0]
        self.assertEqual(url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer groq-key")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "groq-model")

    def test_openrouter_uses_its_own_base_url_and_key(self):
        with mock.patch.object(llm_client.requests, "post", return_value=_completion("ok")) as post:
            result = llm_client._generate_text_openrouter("prompt", 30)

        self.assertEqual(result, "ok")
        url, = post.call_args[0]
        self.assertEqual(url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer or-key")

    def test_base_url_trailing_slash_does_not_double_up(self):
        with mock.patch.object(llm_client, "OPENROUTER_HOST", "https://example.com/v1/"):
            with mock.patch.object(llm_client.requests, "post", return_value=_completion("x")) as post:
                llm_client._generate_text_openrouter("prompt", 30)

        url, = post.call_args[0]
        self.assertEqual(url, "https://example.com/v1/chat/completions")

    def test_attribution_headers_only_sent_when_configured(self):
        with mock.patch.object(llm_client.requests, "post", return_value=_completion("x")) as post:
            llm_client._generate_text_openrouter("prompt", 30)
        self.assertNotIn("HTTP-Referer", post.call_args.kwargs["headers"])

        with mock.patch.object(llm_client, "OPENROUTER_SITE_URL", "https://muckscraper.news"), \
             mock.patch.object(llm_client, "OPENROUTER_APP_NAME", "MuckScraper"):
            with mock.patch.object(llm_client.requests, "post", return_value=_completion("x")) as post:
                llm_client._generate_text_openrouter("prompt", 30)

        self.assertEqual(post.call_args.kwargs["headers"]["HTTP-Referer"], "https://muckscraper.news")
        self.assertEqual(post.call_args.kwargs["headers"]["X-Title"], "MuckScraper")

    def test_missing_api_key_returns_none_without_calling_out(self):
        with mock.patch.object(llm_client, "OPENROUTER_API_KEY", ""):
            with mock.patch.object(llm_client.requests, "post") as post:
                result = llm_client._generate_text_openrouter("prompt", 30)

        self.assertIsNone(result)
        post.assert_not_called()

    def test_rate_limit_retries_then_succeeds(self):
        responses = [
            _FakeResponse(status_code=429, headers={"Retry-After": "0"}),
            _completion("after retry"),
        ]
        with mock.patch.object(llm_client.requests, "post", side_effect=responses) as post, \
             mock.patch.object(llm_client.time, "sleep") as sleep:
            result = llm_client._generate_text_openrouter("prompt", 30)

        self.assertEqual(result, "after retry")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once()

    def test_retries_are_bounded(self):
        always_429 = _FakeResponse(status_code=429, headers={"Retry-After": "0"})
        with mock.patch.object(llm_client.requests, "post", return_value=always_429) as post, \
             mock.patch.object(llm_client.time, "sleep"):
            result = llm_client._generate_text_openrouter("prompt", 30)

        self.assertIsNone(result)
        self.assertEqual(post.call_count, llm_client.OPENAI_COMPATIBLE_MAX_RETRIES + 1)

    def test_retry_wait_is_capped(self):
        responses = [
            _FakeResponse(status_code=429, headers={"Retry-After": "99999"}),
            _completion("x"),
        ]
        with mock.patch.object(llm_client.requests, "post", side_effect=responses), \
             mock.patch.object(llm_client.time, "sleep") as sleep:
            llm_client._generate_text_openrouter("prompt", 30)

        waited, = sleep.call_args[0]
        self.assertLessEqual(waited, llm_client.OPENAI_COMPATIBLE_MAX_RETRY_WAIT_SECONDS)

    def test_network_error_returns_none_rather_than_raising(self):
        with mock.patch.object(llm_client.requests, "post", side_effect=RuntimeError("boom")):
            self.assertIsNone(llm_client._generate_text_openrouter("prompt", 30))

    def test_blank_completion_is_treated_as_no_result(self):
        with mock.patch.object(llm_client.requests, "post", return_value=_completion("   ")):
            self.assertIsNone(llm_client._generate_text_openrouter("prompt", 30))


class ProviderDispatchTests(unittest.TestCase):
    """generate_text / model_for_tier / is_configured routing for the family."""

    def setUp(self):
        self._patchers = [
            mock.patch.object(llm_client, "OPENROUTER_API_KEY", "or-key"),
            mock.patch.object(llm_client, "OPENROUTER_MODEL", "or-quality"),
            mock.patch.object(llm_client, "OPENROUTER_FAST_MODEL", "or-fast"),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patchers])

    def test_generate_text_routes_openrouter_through_shared_path(self):
        # Both tiers pinned to openrouter: per-tier provider routing has its
        # own coverage in test_split_provider_routing.py.
        with mock.patch.object(llm_client, "LLM_PROVIDER", "openrouter"), \
             mock.patch.object(llm_client, "LLM_FAST_PROVIDER", "openrouter"), \
             mock.patch.object(llm_client, "_generate_text_openai_compatible") as shared:
            llm_client.generate_text("prompt", timeout=12, tier=llm_client.TIER_FAST)

        shared.assert_called_once_with("prompt", 12, "or-fast", "openrouter")

    def test_model_for_tier_picks_per_tier_models(self):
        with mock.patch.object(llm_client, "LLM_PROVIDER", "openrouter"), \
             mock.patch.object(llm_client, "LLM_FAST_PROVIDER", "openrouter"):
            self.assertEqual(llm_client.model_for_tier(llm_client.TIER_FAST), "or-fast")
            self.assertEqual(llm_client.model_for_tier(llm_client.TIER_QUALITY), "or-quality")
            # An unrecognized tier degrades to quality, not to an empty name.
            self.assertEqual(llm_client.model_for_tier("nonsense"), "or-quality")

    def test_unset_groq_model_reads_as_not_configured(self):
        # Regression: GROQ_MODEL used to default to "llama-3.1-8b-instant",
        # which Groq has retired -- so the default 404'd on every call and
        # surfaced as a generate error rather than a config problem.
        with mock.patch.object(llm_client, "LLM_PROVIDER", "groq"), \
             mock.patch.object(llm_client, "GROQ_API_KEY", "k"), \
             mock.patch.object(llm_client, "GROQ_MODEL", ""):
            self.assertFalse(llm_client.is_configured())

        with mock.patch.object(llm_client, "LLM_PROVIDER", "groq"), \
             mock.patch.object(llm_client, "GROQ_API_KEY", "k"), \
             mock.patch.object(llm_client, "GROQ_MODEL", "openai/gpt-oss-20b"):
            self.assertTrue(llm_client.is_configured())

    def test_unset_openrouter_model_reads_as_not_configured(self):
        # OPENROUTER_MODEL has no default; without this guard every call would
        # ship an empty model name to the API.
        with mock.patch.object(llm_client, "LLM_PROVIDER", "openrouter"), \
             mock.patch.object(llm_client, "OPENROUTER_MODEL", ""):
            self.assertFalse(llm_client.is_configured())

        with mock.patch.object(llm_client, "LLM_PROVIDER", "openrouter"):
            self.assertTrue(llm_client.is_configured())

    def test_status_surfaces_report_the_active_provider(self):
        # Both tiers on one provider, as on a single-provider install: the
        # detail also reports the fast tier's provider, which is still the
        # import-time default (ollama) unless it is patched too.
        with mock.patch.object(llm_client, "LLM_PROVIDER", "openrouter"), \
             mock.patch.object(llm_client, "LLM_FAST_PROVIDER", "openrouter"), \
             mock.patch.object(llm_client, "_probe_openai_compatible", return_value=True):
            self.assertTrue(llm_client.check_llm_status())
            detail = llm_client.llm_status_detail()

        self.assertEqual(detail["provider"], "openrouter")
        self.assertTrue(detail["online"])
        self.assertIsNone(detail["host"])

    def test_ollama_remains_the_default_provider(self):
        # Nothing in this phase should change an install that never set
        # LLM_PROVIDER, so guard the fallthrough explicitly.
        with mock.patch.object(llm_client, "LLM_PROVIDER", "ollama"), \
             mock.patch.object(llm_client, "OLLAMA_MODEL", "llama"), \
             mock.patch.object(llm_client, "_generate_text_ollama") as ollama:
            llm_client.generate_text("prompt")

        ollama.assert_called_once()


class EmbeddingProviderWarningTests(unittest.TestCase):
    def setUp(self):
        llm_client._warned_embedding_providers.clear()
        self.addCleanup(llm_client._warned_embedding_providers.clear)

    def test_chat_only_provider_warns_but_still_embeds_via_ollama(self):
        with mock.patch.object(llm_client, "EMBEDDING_PROVIDER", "openrouter"), \
             mock.patch.object(llm_client, "_get_embedding_ollama", return_value=[0.1]) as ollama, \
             mock.patch.object(llm_client.logger, "warning") as warn:
            result = llm_client.get_embedding("text")

        self.assertEqual(result, [0.1])
        ollama.assert_called_once()
        self.assertIn("OpenRouter", warn.call_args[0][2])

    def test_warning_is_emitted_once_not_per_article(self):
        with mock.patch.object(llm_client, "EMBEDDING_PROVIDER", "groq"), \
             mock.patch.object(llm_client, "_get_embedding_ollama", return_value=[0.1]), \
             mock.patch.object(llm_client.logger, "warning") as warn:
            for _ in range(5):
                llm_client.get_embedding("text")

        self.assertEqual(warn.call_count, 1)

    def test_supported_providers_stay_silent(self):
        with mock.patch.object(llm_client, "EMBEDDING_PROVIDER", "ollama"), \
             mock.patch.object(llm_client, "_get_embedding_ollama", return_value=[0.1]), \
             mock.patch.object(llm_client.logger, "warning") as warn:
            llm_client.get_embedding("text")

        warn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
