"""Tier-aware health checks and per-provider status -- phase 3 of issue #8.

The design decision this encodes (2026-08-09): degrade, don't stop. A stage
requires its own tier's provider and is skipped if that provider is down; a
fast-provider outage must not block summarization, and vice versa. Under split
routing a partial outage is the normal steady state -- the Ollama box suspends
itself after every run -- so this has to be the behaviour, not an edge case.
"""

import unittest
from unittest import mock

from news_fetcher import llm_client


def _split(quality="openrouter", fast="ollama"):
    return [
        mock.patch.object(llm_client, "LLM_PROVIDER", quality),
        mock.patch.object(llm_client, "LLM_FAST_PROVIDER", fast),
        mock.patch.object(llm_client, "OPENROUTER_API_KEY", "or-key"),
        mock.patch.object(llm_client, "OPENROUTER_MODEL", "cloud"),
        mock.patch.object(llm_client, "OLLAMA_HOST", "http://ollama:11434"),
        mock.patch.object(llm_client, "OLLAMA_FALLBACK_HOST", ""),
        mock.patch.object(llm_client, "OLLAMA_MODEL", "local"),
    ]


class SplitMixin:
    def start(self, **kwargs):
        patchers = _split(**kwargs)
        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers])


class TierAwareStatusTests(SplitMixin, unittest.TestCase):
    def test_each_tier_is_checked_against_its_own_provider(self):
        self.start()
        # Ollama unreachable, cloud key present.
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=False):
            self.assertTrue(llm_client.check_llm_status(llm_client.TIER_QUALITY))
            self.assertFalse(llm_client.check_llm_status(llm_client.TIER_FAST))

    def test_cloud_outage_does_not_take_down_the_fast_tier(self):
        self.start()
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=True), \
             mock.patch.object(llm_client, "OPENROUTER_API_KEY", ""):
            self.assertFalse(llm_client.check_llm_status(llm_client.TIER_QUALITY))
            self.assertTrue(llm_client.check_llm_status(llm_client.TIER_FAST))

    def test_default_tier_is_quality_so_untouched_callers_are_unchanged(self):
        self.start()
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=False):
            self.assertEqual(
                llm_client.check_llm_status(),
                llm_client.check_llm_status(llm_client.TIER_QUALITY),
            )

    def test_check_all_reports_each_provider_separately(self):
        self.start()
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=False):
            self.assertEqual(
                llm_client.check_all_llm_status(),
                {"openrouter": True, "ollama": False},
            )

    def test_single_provider_install_reports_one_entry(self):
        self.start(quality="ollama", fast="ollama")
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=True):
            self.assertEqual(llm_client.check_all_llm_status(), {"ollama": True})


class StatusDetailShapeTests(SplitMixin, unittest.TestCase):
    def test_one_entry_per_distinct_provider_tagged_with_its_tier(self):
        self.start()
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=True):
            detail = llm_client.llm_status_detail()

        self.assertEqual(
            [(e["provider"], e["tier"]) for e in detail["providers"]],
            [("openrouter", "quality"), ("ollama", "fast")],
        )

    def test_single_provider_renders_one_row_not_two(self):
        # Two identical rows would be worse than one, so the count follows
        # distinct providers rather than the number of tiers.
        self.start(quality="ollama", fast="ollama")
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=True):
            detail = llm_client.llm_status_detail()

        self.assertEqual(len(detail["providers"]), 1)

    def test_top_level_online_is_and_across_providers(self):
        self.start()
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=False):
            self.assertFalse(llm_client.llm_status_detail()["online"])
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=True):
            self.assertTrue(llm_client.llm_status_detail()["online"])

    def test_legacy_top_level_keys_survive(self):
        # The sidebar JS falls back to this shape, and external consumers may
        # read it too.
        self.start(quality="ollama", fast="ollama")
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=True):
            detail = llm_client.llm_status_detail()

        for key in ("online", "provider", "host", "role"):
            self.assertIn(key, detail)
        self.assertEqual(detail["provider"], "ollama")
        self.assertEqual(detail["host"], "http://ollama:11434")
        self.assertEqual(detail["role"], "primary")

    def test_fallback_role_is_still_surfaced(self):
        # A sleeping primary covered by the fallback must stay visible -- that
        # is the whole point of the role field.
        patchers = [
            mock.patch.object(llm_client, "LLM_PROVIDER", "ollama"),
            mock.patch.object(llm_client, "LLM_FAST_PROVIDER", "ollama"),
            mock.patch.object(llm_client, "OLLAMA_HOST", "http://primary:11434"),
            mock.patch.object(llm_client, "OLLAMA_FALLBACK_HOST", "http://fallback:11434"),
        ]
        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers])

        def only_fallback(host):
            return host == "http://fallback:11434"

        with mock.patch.object(llm_client, "_probe_ollama_host", side_effect=only_fallback):
            detail = llm_client.llm_status_detail()

        self.assertEqual(detail["providers"][0]["role"], "fallback")
        self.assertTrue(detail["online"])


class PublicStatusLeakTests(SplitMixin, unittest.TestCase):
    """The public route is unauthenticated: host/role must never appear."""

    def test_host_and_role_are_stripped_from_every_entry(self):
        self.start(quality="ollama", fast="ollama")
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=True):
            public = llm_client.llm_status_detail_public()
            admin = llm_client.llm_status_detail()

        # The admin view does expose it, so this is a real difference.
        self.assertEqual(admin["providers"][0]["host"], "http://ollama:11434")
        self.assertNotIn("host", public)
        self.assertNotIn("role", public)
        for entry in public["providers"]:
            self.assertNotIn("host", entry)
            self.assertNotIn("role", entry)

    def test_no_lan_address_appears_anywhere_in_the_public_payload(self):
        self.start(quality="ollama", fast="ollama")
        with mock.patch.object(llm_client, "OLLAMA_HOST", "http://192.168.1.50:11434"), \
             mock.patch.object(llm_client, "_probe_ollama_host", return_value=True):
            payload = repr(llm_client.llm_status_detail_public())

        self.assertNotIn("192.168", payload)

    def test_public_still_carries_what_the_sidebar_needs(self):
        self.start()
        with mock.patch.object(llm_client, "_probe_ollama_host", return_value=True):
            public = llm_client.llm_status_detail_public()

        self.assertIn("online", public)
        self.assertEqual(
            [(e["provider"], e["tier"], e["online"]) for e in public["providers"]],
            [("openrouter", "quality", True), ("ollama", "fast", True)],
        )


if __name__ == "__main__":
    unittest.main()
