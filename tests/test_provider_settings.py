"""Tests for provider settings saved from Settings › LLM providers:
encryption of saved keys (aggregator/secret_box.py), validation
(aggregator/config_store.py) and how llm_client applies overrides."""

import os
import unittest
from unittest import mock

from news_fetcher import llm_client, saved_settings


class SecretBoxTests(unittest.TestCase):
    def test_round_trip_with_explicit_key(self):
        from cryptography.fernet import Fernet
        from aggregator import secret_box
        with mock.patch.dict(os.environ, {"SECRET_KEY": "s" * 32, "CONFIG_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            token = secret_box.encrypt("sk-ant-secret-1234")
            self.assertNotIn("sk-ant-secret", token)
            self.assertEqual(secret_box.decrypt(token), "sk-ant-secret-1234")
            self.assertEqual(secret_box.key_source(), "CONFIG_ENCRYPTION_KEY")

    def test_no_key_means_no_saving_and_no_reading(self):
        # SECRET_KEY is set, but it is never a fallback for the encryption key.
        from cryptography.fernet import Fernet
        from aggregator import secret_box
        with mock.patch.dict(os.environ, {"SECRET_KEY": "s" * 32, "CONFIG_ENCRYPTION_KEY": ""}):
            self.assertIsNone(secret_box.key_source())
            with self.assertRaises(secret_box.KeyNotConfigured):
                secret_box.encrypt("gsk_abc")
            self.assertIsNone(secret_box.decrypt(Fernet(Fernet.generate_key()).encrypt(b"gsk_abc").decode()))

    def test_key_change_makes_saved_secrets_unreadable(self):
        from cryptography.fernet import Fernet
        from aggregator import secret_box
        with mock.patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            token = secret_box.encrypt("gsk_abc")
        with mock.patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            # Another key can't read it: None, not an exception.
            self.assertIsNone(secret_box.decrypt(token))


class ValidateTests(unittest.TestCase):
    def test_kinds(self):
        from aggregator.config_store import validate
        self.assertIsNone(validate("LLM_PROVIDER", "anthropic"))
        self.assertIsNotNone(validate("LLM_PROVIDER", "openai"))
        self.assertIsNone(validate("LLM_FAST_PROVIDER", "main"))
        self.assertIsNotNone(validate("LLM_PROVIDER", "main"))
        self.assertIsNone(validate("OLLAMA_HOST", "http://10.0.0.5:11434", "url"))
        self.assertIsNotNone(validate("OLLAMA_HOST", "javascript:alert(1)", "url"))
        self.assertIsNone(validate("OLLAMA_MODEL", "gemma4:12b", "model"))
        self.assertIsNotNone(validate("OLLAMA_MODEL", "two words", "model"))
        self.assertIsNotNone(validate("GROQ_API_KEY", "has space", "secret"))
        self.assertIsNone(validate("GROQ_API_KEY", "", "secret"))
        with mock.patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": ""}):
            # Without a dedicated key there is nowhere to keep a saved key.
            self.assertIsNotNone(validate("GROQ_API_KEY", "gsk_abc", "secret"))


class SavedSettingsTests(unittest.TestCase):
    def tearDown(self):
        saved_settings.set_source(None)

    def test_saved_value_then_environment_then_default(self):
        saved_settings.set_source(lambda: {"NEWS_API_KEY": "saved-key"})
        with mock.patch.dict(os.environ, {"NEWS_API_KEY": "env-key", "GNEWS_API_KEY": "env-gnews"}):
            self.assertEqual(saved_settings.get("NEWS_API_KEY"), "saved-key")
            self.assertEqual(saved_settings.get("GNEWS_API_KEY"), "env-gnews")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(saved_settings.get("GNEWS_API_KEY", "fallback"), "fallback")

    def test_unreadable_saved_key_stays_blank(self):
        # A saved key that can't be decrypted is "" in the overrides; the
        # environment must not quietly take its place.
        saved_settings.set_source(lambda: {"NEWS_API_KEY": ""})
        with mock.patch.dict(os.environ, {"NEWS_API_KEY": "env-key"}):
            self.assertEqual(saved_settings.get("NEWS_API_KEY"), "")

    def test_unchanged_source_keeps_last_values(self):
        answers = iter([{"NEWS_API_KEY": "k1"}, None, None])
        saved_settings.set_source(lambda: next(answers))
        self.assertEqual(saved_settings.get("NEWS_API_KEY"), "k1")
        self.assertEqual(saved_settings.get("NEWS_API_KEY"), "k1")


class ConfigureTests(unittest.TestCase):
    def tearDown(self):
        llm_client.configure()

    def test_override_beats_environment_and_blank_falls_back(self):
        env = {"LLM_PROVIDER": "ollama", "ANTHROPIC_MODEL": "env-model", "LLM_FAST_PROVIDER": "groq"}
        with mock.patch.dict(os.environ, env):
            llm_client.configure({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k"})
            self.assertEqual(llm_client.LLM_PROVIDER, "anthropic")
            self.assertEqual(llm_client.ANTHROPIC_MODEL, "env-model")
            self.assertEqual(llm_client.ANTHROPIC_FAST_MODEL, "env-model")
            self.assertEqual(llm_client.LLM_FAST_PROVIDER, "groq")

    def test_blank_saved_key_does_not_fall_back_to_environment(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "env-groq-key"}):
            llm_client.configure({"GROQ_API_KEY": ""})
            self.assertEqual(llm_client.GROQ_API_KEY, "")
            llm_client.configure({})
            self.assertEqual(llm_client.GROQ_API_KEY, "env-groq-key")

    def test_same_as_main_undoes_environment_fast_provider(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "ollama", "LLM_FAST_PROVIDER": "groq"}):
            llm_client.configure({"LLM_PROVIDER": "gemini", "LLM_FAST_PROVIDER": "main"})
            self.assertEqual(llm_client.LLM_FAST_PROVIDER, "gemini")

    def test_config_source_applies_on_sync(self):
        calls = []

        def source():
            calls.append(1)
            return {"LLM_PROVIDER": "groq", "GROQ_API_KEY": "x", "GROQ_MODEL": "m"} if len(calls) == 1 else None

        saved_settings.set_source(source)
        try:
            self.assertEqual(llm_client.provider_for_tier(llm_client.TIER_QUALITY), "groq")
            self.assertTrue(llm_client.is_configured(llm_client.TIER_QUALITY))
        finally:
            saved_settings.set_source(None)

    def test_failing_source_keeps_settings(self):
        calls = []

        def source():
            calls.append(1)
            if len(calls) == 1:
                return {"LLM_PROVIDER": "gemini"}
            raise RuntimeError("db down")

        saved_settings.set_source(source)
        try:
            self.assertEqual(llm_client.provider_for_tier(llm_client.TIER_QUALITY), "gemini")
            # The next read fails; the last good settings stay in force.
            self.assertEqual(llm_client.provider_for_tier(llm_client.TIER_QUALITY), "gemini")
            self.assertEqual(len(calls), 2)
        finally:
            saved_settings.set_source(None)


if __name__ == "__main__":
    unittest.main()
