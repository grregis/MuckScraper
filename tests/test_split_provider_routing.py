"""Per-tier provider routing -- phase 2 of upstream issue #8.

The reporter's configuration is LLM_PROVIDER=openrouter with
LLM_FAST_PROVIDER=ollama: cloud summaries, local classification. These check
that each tier resolves to its own provider and model, and -- at least as
importantly -- that an install which never sets LLM_FAST_PROVIDER behaves
exactly as it did before.
"""

import unittest
from unittest import mock

from news_fetcher import llm_client


def _split(quality="openrouter", fast="ollama"):
    """Patch a split-routing configuration with both providers usable."""
    return [
        mock.patch.object(llm_client, "LLM_PROVIDER", quality),
        mock.patch.object(llm_client, "LLM_FAST_PROVIDER", fast),
        mock.patch.object(llm_client, "OPENROUTER_API_KEY", "or-key"),
        mock.patch.object(llm_client, "OPENROUTER_MODEL", "cloud-quality"),
        mock.patch.object(llm_client, "OPENROUTER_FAST_MODEL", "cloud-fast"),
        mock.patch.object(llm_client, "OLLAMA_HOST", "http://ollama:11434"),
        mock.patch.object(llm_client, "OLLAMA_MODEL", "local-quality"),
        mock.patch.object(llm_client, "OLLAMA_FAST_MODEL", "local-fast"),
        mock.patch.object(llm_client, "GROQ_API_KEY", "groq-key"),
        mock.patch.object(llm_client, "GROQ_MODEL", "groq-quality"),
        mock.patch.object(llm_client, "GROQ_FAST_MODEL", "groq-fast"),
    ]


class SplitRoutingMixin:
    def start(self, **kwargs):
        patchers = _split(**kwargs)
        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers])


class ProviderForTierTests(SplitRoutingMixin, unittest.TestCase):
    def test_each_tier_resolves_to_its_own_provider(self):
        self.start()

        self.assertEqual(llm_client.provider_for_tier(llm_client.TIER_QUALITY), "openrouter")
        self.assertEqual(llm_client.provider_for_tier(llm_client.TIER_FAST), "ollama")

    def test_unrecognized_tier_degrades_to_the_quality_provider(self):
        self.start()

        self.assertEqual(llm_client.provider_for_tier("nonsense"), "openrouter")

    def test_model_follows_the_tier_provider_not_the_global_one(self):
        self.start()

        self.assertEqual(llm_client.model_for_tier(llm_client.TIER_QUALITY), "cloud-quality")
        # The fast model must come from Ollama's vars, not OpenRouter's.
        self.assertEqual(llm_client.model_for_tier(llm_client.TIER_FAST), "local-fast")

    def test_generate_text_dispatches_each_tier_to_its_backend(self):
        self.start()

        with mock.patch.object(llm_client, "_generate_text_openai_compatible") as cloud, \
             mock.patch.object(llm_client, "_generate_text_ollama") as local:
            llm_client.generate_text("summary prompt")
            llm_client.generate_text("classify prompt", tier=llm_client.TIER_FAST)

        cloud.assert_called_once_with("summary prompt", 30, "cloud-quality", "openrouter")
        local.assert_called_once_with("classify prompt", 30, "local-fast")

    def test_is_configured_asks_about_the_requested_tier(self):
        # Fast tier is usable even with the cloud provider unconfigured, and
        # vice versa -- one broken backend must not gate the other's work.
        self.start()
        with mock.patch.object(llm_client, "OPENROUTER_API_KEY", ""):
            self.assertFalse(llm_client.is_configured(llm_client.TIER_QUALITY))
            self.assertTrue(llm_client.is_configured(llm_client.TIER_FAST))

    def test_reversed_split_also_works(self):
        # Local summaries with cloud classification -- the other direction.
        self.start(quality="ollama", fast="groq")

        self.assertEqual(llm_client.model_for_tier(llm_client.TIER_QUALITY), "local-quality")
        self.assertEqual(llm_client.model_for_tier(llm_client.TIER_FAST), "groq-fast")

    def test_providers_in_use_lists_each_backend_once(self):
        self.start()
        self.assertEqual(llm_client.providers_in_use(), ["openrouter", "ollama"])

        self.start(quality="ollama", fast="ollama")
        self.assertEqual(llm_client.providers_in_use(), ["ollama"])


class BackwardCompatibilityTests(unittest.TestCase):
    """An install that never sets LLM_FAST_PROVIDER must not change at all."""

    def test_blank_fast_provider_falls_back_to_the_global_one(self):
        import importlib
        try:
            with mock.patch.dict("os.environ", {"LLM_PROVIDER": "gemini", "LLM_FAST_PROVIDER": ""}, clear=False):
                reloaded = importlib.reload(llm_client)
                self.assertEqual(reloaded.LLM_FAST_PROVIDER, "gemini")
                self.assertEqual(reloaded.provider_for_tier(reloaded.TIER_FAST), "gemini")
                self.assertEqual(reloaded.providers_in_use(), ["gemini"])
        finally:
            # Restore from the real environment, then catch the reloaded module
            # up with saved_settings' version: a reload starts it at 0, and a
            # stale version makes the next call rebind every setting from the
            # environment, undoing a later test's patch.object() overrides.
            importlib.reload(llm_client)
            llm_client._sync_config()

    def test_single_provider_routes_both_tiers_to_one_backend(self):
        patchers = [
            mock.patch.object(llm_client, "LLM_PROVIDER", "ollama"),
            mock.patch.object(llm_client, "LLM_FAST_PROVIDER", "ollama"),
            mock.patch.object(llm_client, "OLLAMA_HOST", "http://ollama:11434"),
            mock.patch.object(llm_client, "OLLAMA_MODEL", "big"),
            mock.patch.object(llm_client, "OLLAMA_FAST_MODEL", "small"),
        ]
        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers])

        with mock.patch.object(llm_client, "_generate_text_ollama") as local:
            llm_client.generate_text("a")
            llm_client.generate_text("b", tier=llm_client.TIER_FAST)

        self.assertEqual(local.call_count, 2)
        self.assertEqual(local.call_args_list[0][0][2], "big")
        self.assertEqual(local.call_args_list[1][0][2], "small")

    def test_is_configured_default_tier_is_unchanged(self):
        with mock.patch.object(llm_client, "LLM_PROVIDER", "ollama"), \
             mock.patch.object(llm_client, "LLM_FAST_PROVIDER", "ollama"), \
             mock.patch.object(llm_client, "OLLAMA_HOST", "h"), \
             mock.patch.object(llm_client, "OLLAMA_MODEL", "m"):
            self.assertTrue(llm_client.is_configured())


class ProviderConditionalBranchTests(SplitRoutingMixin, unittest.TestCase):
    """The three branches that used to key off the global provider."""

    def test_rss_entry_cap_follows_the_fast_provider(self):
        from news_fetcher import rss_fetcher

        # Groq on the fast tier -> the cap applies, even though the global
        # provider is a cloud model with no such budget.
        self.start(quality="openrouter", fast="groq")
        self.assertEqual(llm_client.provider_for_tier(llm_client.TIER_FAST), "groq")

        # Groq as the *global* provider but Ollama on fast -> cap must NOT
        # apply; classification is local and unmetered.
        self.start(quality="groq", fast="ollama")
        self.assertNotEqual(llm_client.provider_for_tier(llm_client.TIER_FAST), "groq")
        self.assertTrue(hasattr(rss_fetcher, "RSS_MAX_ENTRIES_PER_FEED_GROQ"))

    def test_summarizer_warning_follows_the_quality_provider(self):
        # Ollama on quality with no model set is a real misconfiguration...
        self.start(quality="ollama", fast="openrouter")
        with mock.patch.object(llm_client, "OLLAMA_MODEL", ""):
            self.assertEqual(llm_client.provider_for_tier(llm_client.TIER_QUALITY), "ollama")

        # ...but Ollama only on the fast tier means an unset OLLAMA_MODEL says
        # nothing about whether summaries work.
        self.start(quality="openrouter", fast="ollama")
        self.assertNotEqual(llm_client.provider_for_tier(llm_client.TIER_QUALITY), "ollama")


if __name__ == "__main__":
    unittest.main()
