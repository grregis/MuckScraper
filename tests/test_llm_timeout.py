"""Tests for the configurable LLM request timeout (issue #1).

`generate_text` default timeout comes from the `LLM_TIMEOUT` env var
(default 60s) instead of a hardcoded 30. Callers that pass an explicit
timeout (summarizer: 120/180/150) bypass the default - this test guards
that contract so the summarizer regression can't sneak back in.
"""

import unittest
from unittest import mock

from news_fetcher import llm_client


class GenerateTextTimeoutTest(unittest.TestCase):
    def test_default_uses_LLM_TIMEOUT(self):
        """No timeout arg -> the env-driven LLM_TIMEOUT default is used."""
        with mock.patch.object(llm_client, "LLM_TIMEOUT", 60), \
                mock.patch.object(
                    llm_client, "_generate_text_ollama", return_value="ok"
                ) as m:
            llm_client.generate_text("prompt")
            m.assert_called_once_with("prompt", 60)

    def test_explicit_timeout_overrides_default(self):
        """An explicit timeout arg bypasses LLM_TIMEOUT (summarizer contract)."""
        with mock.patch.object(llm_client, "LLM_TIMEOUT", 60), \
                mock.patch.object(
                    llm_client, "_generate_text_ollama", return_value="ok"
                ) as m:
            llm_client.generate_text("prompt", timeout=120)
            m.assert_called_once_with("prompt", 120)

    def test_explicit_none_falls_back_to_default(self):
        """Passing timeout=None explicitly still picks up LLM_TIMEOUT."""
        with mock.patch.object(llm_client, "LLM_TIMEOUT", 90), \
                mock.patch.object(
                    llm_client, "_generate_text_ollama", return_value="ok"
                ) as m:
            llm_client.generate_text("prompt", timeout=None)
            m.assert_called_once_with("prompt", 90)

    def test_LLM_TIMEOUT_is_positive_int(self):
        """The module-level default parsed from env is a usable timeout."""
        self.assertIsInstance(llm_client.LLM_TIMEOUT, int)
        self.assertGreater(llm_client.LLM_TIMEOUT, 0)


if __name__ == "__main__":
    unittest.main()