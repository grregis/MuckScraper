"""Inference-level health gate (2026-09-30).

On 2026-09-28 Ollama's HTTP server answered while every real inference call
timed out, so the run ground for hours. These pin: the probe tells wedged
from asleep from healthy, a wedged flag makes Ollama calls fail fast and the
status gate read down, and the scheduler checkpoint sets/clears it.
"""

import unittest
from unittest import mock

import requests

from news_fetcher import llm_client
from news_fetcher import scheduler

HOST = "http://primary:11434"


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class InferenceGateTests(unittest.TestCase):
    def setUp(self):
        llm_client.set_inference_wedged(False)
        llm_client._ollama_state["active"] = None
        self._p = [
            mock.patch.object(llm_client, "OLLAMA_HOST", HOST),
            mock.patch.object(llm_client, "OLLAMA_FALLBACK_HOST", ""),
            mock.patch.object(llm_client, "LLM_PROVIDER", "ollama"),
            mock.patch.object(llm_client, "LLM_FAST_PROVIDER", "ollama"),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        llm_client.set_inference_wedged(False)

    def test_probe_healthy(self):
        with mock.patch.object(llm_client.requests, "get", return_value=_Resp(200)), \
             mock.patch.object(llm_client.requests, "post", return_value=_Resp(200, {"embedding": [0.1]})):
            self.assertIs(llm_client.probe_ollama_inference(), True)

    def test_probe_wedged_when_http_up_but_embedding_times_out(self):
        with mock.patch.object(llm_client.requests, "get", return_value=_Resp(200)), \
             mock.patch.object(llm_client.requests, "post", side_effect=requests.Timeout("read timeout")) as post:
            self.assertIs(llm_client.probe_ollama_inference(), False)
            self.assertEqual(post.call_count, llm_client._INFERENCE_PROBE_ATTEMPTS)

    def test_probe_retry_recovers_from_one_slow_call(self):
        with mock.patch.object(llm_client.requests, "get", return_value=_Resp(200)), \
             mock.patch.object(llm_client.requests, "post",
                               side_effect=[requests.Timeout("cold"), _Resp(200, {"embedding": [0.1]})]):
            self.assertIs(llm_client.probe_ollama_inference(), True)

    def test_probe_none_when_asleep(self):
        with mock.patch.object(llm_client.requests, "get", side_effect=requests.ConnectionError("no route")), \
             mock.patch.object(llm_client.requests, "post") as post:
            self.assertIsNone(llm_client.probe_ollama_inference())
            post.assert_not_called()

    def test_probe_wedged_when_connect_succeeds_but_tags_never_answers(self):
        # 2026-10-11: port open, every call hung; this used to read as asleep.
        with mock.patch.object(llm_client.requests, "get", side_effect=requests.exceptions.ReadTimeout("hung")), \
             mock.patch.object(llm_client.requests, "post") as post:
            self.assertIs(llm_client.probe_ollama_inference(), False)
            post.assert_not_called()

    def test_probe_none_when_connect_times_out(self):
        # A powered-off box: the connect itself times out.
        with mock.patch.object(llm_client.requests, "get", side_effect=requests.exceptions.ConnectTimeout("off")):
            self.assertIsNone(llm_client.probe_ollama_inference())

    def test_wedged_flag_fails_calls_fast_and_reads_down(self):
        llm_client.set_inference_wedged(True)
        with mock.patch.object(llm_client.requests, "get", return_value=_Resp(200)), \
             mock.patch.object(llm_client.requests, "post") as post:
            self.assertIsNone(llm_client.get_embedding("x"))
            self.assertIsNone(llm_client.generate_text("x"))
            self.assertFalse(llm_client.check_llm_status(llm_client.TIER_FAST))
            post.assert_not_called()

    def test_checkpoint_sets_then_clears_flag(self):
        state = {"checks": [], "went_down_during_run": False, "inference_wedged": False}
        with mock.patch.object(llm_client, "probe_ollama_inference", return_value=False), \
             mock.patch.object(llm_client, "check_all_llm_status", side_effect=lambda: {"ollama": not llm_client.inference_wedged()}):
            self.assertFalse(scheduler._check_ollama_status_for_report(state, "run_start"))
            self.assertTrue(llm_client.inference_wedged())
            self.assertTrue(state["inference_wedged"])
        with mock.patch.object(llm_client, "probe_ollama_inference", return_value=True), \
             mock.patch.object(llm_client, "check_all_llm_status", return_value={"ollama": True}):
            self.assertTrue(scheduler._check_ollama_status_for_report(state, "before_headline_ranking"))
            self.assertFalse(llm_client.inference_wedged())

    def test_asleep_box_does_not_set_wedged(self):
        state = {"checks": [], "went_down_during_run": False, "inference_wedged": False}
        with mock.patch.object(llm_client, "probe_ollama_inference", return_value=None), \
             mock.patch.object(llm_client, "check_all_llm_status", return_value={"ollama": False}):
            scheduler._check_ollama_status_for_report(state, "run_start")
        self.assertFalse(llm_client.inference_wedged())
        self.assertFalse(state["inference_wedged"])

    def test_run_end_clears_without_probing(self):
        llm_client.set_inference_wedged(True)
        state = {"checks": [], "went_down_during_run": False, "inference_wedged": True}
        with mock.patch.object(llm_client, "probe_ollama_inference") as probe, \
             mock.patch.object(llm_client, "check_all_llm_status", return_value={"ollama": True}):
            scheduler._check_ollama_status_for_report(state, "run_end")
            probe.assert_not_called()
        self.assertFalse(llm_client.inference_wedged())


if __name__ == "__main__":
    unittest.main()
