"""Status checks probe OpenAI-compatible endpoints instead of trusting the key.

2026-11-08: OPENROUTER_HOST pointed at a llama-server on the LAN that was not
running. The status check only asked "is a key set?", so the analyse-all step
went ahead and every call was refused. These pin the probe's verdicts and its
cache.
"""
import unittest
from unittest import mock

import requests

from news_fetcher import llm_client


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class OpenAICompatibleProbeTests(unittest.TestCase):
    def setUp(self):
        patchers = [
            mock.patch.object(llm_client, "LLM_PROVIDER", "openrouter"),
            mock.patch.object(llm_client, "LLM_FAST_PROVIDER", "openrouter"),
            mock.patch.object(llm_client, "OPENROUTER_HOST", "http://llm.lan:8080/v1"),
            mock.patch.object(llm_client, "OPENROUTER_API_KEY", "local-key"),
            mock.patch.object(llm_client, "OPENROUTER_MODEL", "local-model"),
            mock.patch.object(llm_client, "_sync_config", lambda: None),
            mock.patch.dict(llm_client._openai_compatible_probe_cache, clear=True),
        ]
        for p in patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patchers])

    def _status_with(self, **get_kwargs):
        with mock.patch.object(llm_client.requests, "get", **get_kwargs) as get:
            result = llm_client.check_llm_status(llm_client.TIER_QUALITY)
        return result, get

    def test_answering_endpoint_is_up(self):
        online, get = self._status_with(return_value=_Resp(200))
        self.assertTrue(online)
        url = get.call_args.args[0]
        self.assertEqual(url, "http://llm.lan:8080/v1/models")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer local-key")

    def test_refused_connection_is_down_even_with_a_key(self):
        online, _ = self._status_with(side_effect=requests.exceptions.ConnectionError("refused"))
        self.assertFalse(online)

    def test_timeout_is_down(self):
        online, _ = self._status_with(side_effect=requests.exceptions.ReadTimeout())
        self.assertFalse(online)

    def test_server_error_and_auth_refusal_are_down(self):
        for code in (500, 502, 503, 401, 403):
            llm_client._openai_compatible_probe_cache.clear()
            online, _ = self._status_with(return_value=_Resp(code))
            self.assertFalse(online, code)

    def test_no_models_route_or_rate_limit_still_counts_as_up(self):
        for code in (404, 429):
            llm_client._openai_compatible_probe_cache.clear()
            online, _ = self._status_with(return_value=_Resp(code))
            self.assertTrue(online, code)

    def test_no_key_is_down_without_a_request(self):
        with mock.patch.object(llm_client, "OPENROUTER_API_KEY", ""):
            online, get = self._status_with(return_value=_Resp(200))
        self.assertFalse(online)
        get.assert_not_called()

    def test_result_is_cached_briefly(self):
        with mock.patch.object(llm_client.requests, "get", return_value=_Resp(200)) as get:
            self.assertTrue(llm_client.check_llm_status())
            self.assertTrue(llm_client.check_llm_status())
        self.assertEqual(get.call_count, 1)

    def test_cache_expires(self):
        clock = iter([0.0, llm_client._OPENAI_COMPATIBLE_PROBE_TTL + 1])
        with mock.patch.object(llm_client.time, "monotonic", side_effect=lambda: next(clock)), \
             mock.patch.object(llm_client.requests, "get",
                               side_effect=[_Resp(200), requests.exceptions.ConnectionError()]) as get:
            self.assertTrue(llm_client.check_llm_status())
            self.assertFalse(llm_client.check_llm_status())
        self.assertEqual(get.call_count, 2)

    def test_changed_host_is_probed_again(self):
        with mock.patch.object(llm_client.requests, "get", return_value=_Resp(200)) as get:
            llm_client.check_llm_status()
            with mock.patch.object(llm_client, "OPENROUTER_HOST", "http://other.lan/v1"):
                llm_client.check_llm_status()
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.args[0], "http://other.lan/v1/models")

    def test_status_detail_reports_the_probe(self):
        with mock.patch.object(llm_client.requests, "get",
                               side_effect=requests.exceptions.ConnectionError()):
            detail = llm_client.llm_status_detail()
        self.assertFalse(detail["online"])
        self.assertEqual(detail["provider"], "openrouter")


if __name__ == "__main__":
    unittest.main()
