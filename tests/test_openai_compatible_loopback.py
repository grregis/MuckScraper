"""End-to-end check of the OpenAI-compatible client against a real HTTP server.

The mocked tests in test_openai_compatible_provider.py assert that the code
agrees with a mock. These assert it builds a correct HTTP request: a real
socket, real `requests` serialization, a real server parsing the URL, headers
and JSON body. That is the gap a mock cannot cover -- a wrong path, a
malformed auth header or a bad body shape all pass a mock happily.

No API key or network access needed: OPENROUTER_HOST points at a loopback
server, which is exactly the "any OpenAI-compatible endpoint" case the
generalization is meant to support.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from news_fetcher import llm_client


class _Recorder(BaseHTTPRequestHandler):
    requests_seen = []
    reply_429_times = 0

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _Recorder.requests_seen.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })

        if _Recorder.reply_429_times > 0:
            _Recorder.reply_429_times -= 1
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
            self.wfile.write(b"{}")
            return

        payload = json.dumps({
            "choices": [{"message": {"content": f"echo:{body.get('model')}"}}]
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass  # keep the test output clean


class OpenAICompatibleLoopbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Recorder)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Recorder.requests_seen = []
        _Recorder.reply_429_times = 0
        base = f"http://127.0.0.1:{self.port}/v1"
        self._patchers = [
            mock.patch.object(llm_client, "LLM_PROVIDER", "openrouter"),
            mock.patch.object(llm_client, "LLM_FAST_PROVIDER", "openrouter"),
            mock.patch.object(llm_client, "OPENROUTER_HOST", base),
            mock.patch.object(llm_client, "OPENROUTER_API_KEY", "test-key"),
            mock.patch.object(llm_client, "OPENROUTER_MODEL", "quality-model"),
            mock.patch.object(llm_client, "OPENROUTER_FAST_MODEL", "fast-model"),
            mock.patch.object(llm_client, "OPENROUTER_SITE_URL", ""),
            mock.patch.object(llm_client, "OPENROUTER_APP_NAME", ""),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patchers])

    def test_real_request_reaches_the_expected_path_and_returns_content(self):
        result = llm_client.generate_text("hello", timeout=5)

        self.assertEqual(result, "echo:quality-model")
        self.assertEqual(len(_Recorder.requests_seen), 1)
        self.assertEqual(_Recorder.requests_seen[0]["path"], "/v1/chat/completions")

    def test_auth_header_and_body_shape_are_what_the_api_expects(self):
        llm_client.generate_text("hello there", timeout=5)
        seen = _Recorder.requests_seen[0]

        self.assertEqual(seen["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(seen["body"]["messages"], [{"role": "user", "content": "hello there"}])
        self.assertEqual(seen["body"]["model"], "quality-model")

    def test_fast_tier_sends_the_fast_model_over_the_wire(self):
        result = llm_client.generate_text("x", timeout=5, tier=llm_client.TIER_FAST)

        self.assertEqual(result, "echo:fast-model")
        self.assertEqual(_Recorder.requests_seen[0]["body"]["model"], "fast-model")

    def test_attribution_headers_travel_when_set(self):
        with mock.patch.object(llm_client, "OPENROUTER_SITE_URL", "https://muckscraper.news"), \
             mock.patch.object(llm_client, "OPENROUTER_APP_NAME", "MuckScraper"):
            llm_client.generate_text("x", timeout=5)

        seen = _Recorder.requests_seen[0]
        self.assertEqual(seen["headers"]["HTTP-Referer"], "https://muckscraper.news")
        self.assertEqual(seen["headers"]["X-Title"], "MuckScraper")

    def test_real_429_is_retried_and_then_succeeds(self):
        _Recorder.reply_429_times = 1

        with mock.patch.object(llm_client.time, "sleep") as sleep:
            result = llm_client.generate_text("x", timeout=5)

        self.assertEqual(result, "echo:quality-model")
        self.assertEqual(len(_Recorder.requests_seen), 2)
        sleep.assert_called_once()

    def test_persistent_429_gives_up_without_raising(self):
        _Recorder.reply_429_times = 99

        with mock.patch.object(llm_client.time, "sleep"):
            result = llm_client.generate_text("x", timeout=5)

        self.assertIsNone(result)
        self.assertEqual(len(_Recorder.requests_seen), llm_client.OPENAI_COMPATIBLE_MAX_RETRIES + 1)

    def test_unreachable_endpoint_returns_none_rather_than_raising(self):
        # A dead port is the closest stand-in for a provider outage.
        with mock.patch.object(llm_client, "OPENROUTER_HOST", "http://127.0.0.1:1/v1"):
            self.assertIsNone(llm_client.generate_text("x", timeout=2))

    def test_groq_config_drives_the_same_loopback_server(self):
        # Proves the two providers really are one implementation: same server,
        # same assertions, only the config differs.
        base = f"http://127.0.0.1:{self.port}/openai/v1"
        with mock.patch.object(llm_client, "LLM_PROVIDER", "groq"), \
             mock.patch.object(llm_client, "LLM_FAST_PROVIDER", "groq"), \
             mock.patch.object(llm_client, "GROQ_HOST", base), \
             mock.patch.object(llm_client, "GROQ_API_KEY", "groq-key"), \
             mock.patch.object(llm_client, "GROQ_MODEL", "groq-model"):
            result = llm_client.generate_text("x", timeout=5)

        seen = _Recorder.requests_seen[0]
        self.assertEqual(result, "echo:groq-model")
        self.assertEqual(seen["path"], "/openai/v1/chat/completions")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer groq-key")


if __name__ == "__main__":
    unittest.main()
