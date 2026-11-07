"""OLLAMA_THINK: blank sends nothing (Ollama's default), true/false send "think"."""

import unittest
from unittest import mock

from news_fetcher import llm_client


class _FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"response": " OK "}


class ParseThinkTests(unittest.TestCase):
    def test_blank_and_default_mean_unset(self):
        for value in ("", "  ", "default", None):
            self.assertIsNone(llm_client._parse_think(value))

    def test_false_values(self):
        for value in ("false", "False", "0", "no", "off"):
            self.assertIs(llm_client._parse_think(value), False)

    def test_true_values(self):
        for value in ("true", "TRUE", "1", "yes", "on"):
            self.assertIs(llm_client._parse_think(value), True)

    def test_unrecognised_is_unset(self):
        self.assertIsNone(llm_client._parse_think("maybe"))


class GenerateBodyTests(unittest.TestCase):
    def setUp(self):
        llm_client._ollama_state["active"] = None
        llm_client._ollama_state["next_primary_probe"] = 0.0
        for p in (mock.patch.object(llm_client, "OLLAMA_HOST", "http://primary:11434"),
                  mock.patch.object(llm_client, "OLLAMA_FALLBACK_HOST", ""),
                  mock.patch.object(llm_client, "OLLAMA_MODEL", "test-model")):
            p.start()
            self.addCleanup(p.stop)

    def _body(self, think):
        with mock.patch.object(llm_client, "OLLAMA_THINK", think), \
                mock.patch.object(llm_client.requests, "post", return_value=_FakeResponse()) as post:
            self.assertEqual(llm_client._generate_text_ollama("hi", 30), "OK")
        return post.call_args.kwargs["json"]

    def test_unset_sends_no_think_key(self):
        self.assertNotIn("think", self._body(None))

    def test_false_sends_think_false(self):
        self.assertIs(self._body(False)["think"], False)

    def test_true_sends_think_true(self):
        self.assertIs(self._body(True)["think"], True)

    def test_never_sends_num_ctx(self):
        body = self._body(False)
        self.assertNotIn("options", body)
        self.assertEqual(body["model"], "test-model")


if __name__ == "__main__":
    unittest.main()
