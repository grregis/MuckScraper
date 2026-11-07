"""Pure-logic tests for mcp_server/verdicts.py (no database, no network)."""
import unittest
from datetime import datetime

from mcp_server import verdicts as v


class RunStatusTests(unittest.TestCase):
    NOW = datetime(2026, 10, 7, 1, 0)

    def test_running_and_stale(self):
        self.assertEqual(v.run_in_progress({"status": "running", "started_at": "2026-10-07T00:30:00"}, self.NOW), (True, False))
        self.assertEqual(v.run_in_progress({"status": "running", "started_at": "2026-10-06T20:00:00"}, self.NOW), (False, True))
        self.assertEqual(v.run_in_progress({"status": "idle"}, self.NOW), (False, False))
        self.assertEqual(v.run_in_progress(None, self.NOW), (False, False))

    def test_upcoming_runs_in_eastern_time(self):
        rows = [(7, True), (12, False), (17, True), (22, False)]
        runs = v.upcoming_runs(rows, self.NOW)  # 01:00 UTC = 21:00 EDT
        self.assertEqual(runs[0], {"at_utc": "2026-10-07T02:00", "at_local": "2026-10-06 22:00 EDT", "type": "fetch-only"})
        self.assertEqual(runs[1]["at_utc"], "2026-10-07T11:00")
        self.assertEqual(runs[1]["type"], "full")

    def test_missed_slot(self):
        rows = [(7, True), (17, True)]
        slot = v.latest_slot_before(rows, datetime(2026, 10, 6, 12, 39))  # the 10-06 late run
        self.assertEqual(slot, datetime(2026, 10, 6, 11, 0))
        level, text = v.run_status_verdict(False, False, {}, datetime(2026, 10, 6, 3, 4), slot, [], datetime(2026, 10, 6, 12, 39))
        self.assertEqual(level, v.WARNING)
        self.assertIn("has not happened", text)

    def test_running_verdict_warns_against_restarts(self):
        level, text = v.run_status_verdict(True, False, {"started_at": "2026-10-07T00:30:00", "run_full_pipeline": True},
                                           None, None, [], self.NOW)
        self.assertEqual(level, v.WARNING)
        self.assertIn("full run is in progress (30 min", text)
        self.assertIn("Do not restart", text)

    def test_idle(self):
        level, text = v.run_status_verdict(False, False, {}, datetime(2026, 10, 7, 0, 50), datetime(2026, 10, 6, 21, 0), [], self.NOW)
        self.assertEqual(level, v.OK)


class OllamaVerdictTests(unittest.TestCase):
    def test_asleep_after_run_is_normal(self):
        self.assertEqual(v.ollama_verdict({"result": "timeout"}, {}, True)[0], v.OK)

    def test_unexplained_timeout_is_a_problem(self):
        level, text = v.ollama_verdict({"result": "timeout"}, {}, False)
        self.assertEqual(level, v.PROBLEM)
        self.assertIn("OLLAMA_MAC", text)

    def test_refused_and_hung(self):
        self.assertIn("service is not", v.ollama_verdict({"result": "refused"}, {}, False)[1])
        self.assertIn("hung", v.ollama_verdict({"result": "hung"}, {}, False)[1])

    def test_partial_gpu_offload(self):
        probe = {"result": "ok", "models": [{"name": "big", "size": 100, "size_vram": 60},
                                            {"name": "embed", "size": 10, "size_vram": 10}]}
        level, text = v.ollama_verdict(probe, {}, False)
        self.assertEqual(level, v.WARNING)
        self.assertIn("big", text)
        self.assertNotIn("embed", text)

    def test_healthy_but_last_run_wedged(self):
        level, text = v.ollama_verdict({"result": "ok", "models": []}, {"inference_wedged": True}, False)
        self.assertEqual(level, v.WARNING)
        self.assertIn("reboot", text)

    def test_other_client_context(self):
        probe = {"result": "ok", "models": [{"name": "gemma4:e4b-it-qat", "size": 3, "size_vram": 3, "context_length": 65536}]}
        level, text = v.ollama_verdict(probe, {"inference_wedged": True}, False, expected_context=32768)
        self.assertEqual(level, v.WARNING)
        self.assertIn("Another client", text)
        self.assertEqual(v.ollama_verdict(
            {"result": "ok", "models": [{"name": "m", "size": 3, "size_vram": 3, "context_length": 32768}]},
            {}, False, expected_context=32768)[0], v.OK)

    def test_parse_expected_contexts(self):
        self.assertEqual(v.parse_expected_contexts("65536"), {"*": 65536})
        self.assertEqual(v.parse_expected_contexts("65536, gemma4-12b-16k:latest=16384"),
                         {"*": 65536, "gemma4-12b-16k": 16384})
        self.assertEqual(v.parse_expected_contexts(""), {})
        self.assertEqual(v.parse_expected_contexts("abc,x=,=5"), {})

    def test_per_model_context(self):
        expected = v.parse_expected_contexts("65536,gemma4-12b-16k=16384")
        ok = {"result": "ok", "models": [
            {"name": "gemma4-12b-16k:latest", "size": 7, "size_vram": 7, "context_length": 16384},
            {"name": "nomic-embed-text:latest", "size": 1, "size_vram": 1, "context_length": 65536}]}
        self.assertEqual(v.ollama_verdict(ok, {}, False, expected)[0], v.OK)
        other = {"result": "ok", "models": [
            {"name": "gemma4-12b-16k:latest", "size": 7, "size_vram": 7, "context_length": 16384},
            {"name": "gemma4:e4b-it-qat", "size": 3, "size_vram": 3, "context_length": 32768}]}
        level, text = v.ollama_verdict(other, {}, False, expected)
        self.assertEqual(level, v.WARNING)
        self.assertIn("gemma4:e4b-it-qat (32768, expected 65536)", text)
        self.assertNotIn("gemma4-12b-16k", text)

    def test_not_configured(self):
        self.assertEqual(v.ollama_verdict({"result": "not_configured"}, None, False)[0], v.OK)


class OutageTests(unittest.TestCase):
    def test_missing_embeddings(self):
        level, text = v.outage_verdict(205, 72, 70, 0.9, 0.7)
        self.assertEqual(level, v.PROBLEM)
        self.assertIn("72 of 205", text)
        self.assertIn("scoped recipe", text)

    def test_clean(self):
        self.assertEqual(v.outage_verdict(240, 0, 10, 0.72, 0.72)[0], v.OK)

    def test_empty_window(self):
        self.assertEqual(v.outage_verdict(0, 0, 0, 0, 0.7)[0], v.WARNING)


class CodeLiveTests(unittest.TestCase):
    def test_stale_and_current(self):
        out = v.code_live_verdict(
            {"scheduler": datetime(2026, 8, 27, 20, 49), "app": datetime(2026, 8, 28, 1, 0), "x": None},
            datetime(2026, 8, 27, 21, 31))
        self.assertTrue(out["scheduler"].startswith("STALE"))
        self.assertTrue(out["app"].startswith("current"))
        self.assertEqual(out["x"], "unknown")


class HistoryTests(unittest.TestCase):
    def test_flags(self):
        entries = [{"finished_at": str(i), "duration_min": 60, "stored": 200, "status": "ok"} for i in range(6)]
        entries.append({"finished_at": "slow", "duration_min": 200, "stored": 30, "status": "error"})
        flags = v.history_flags(entries)["flags"]
        self.assertTrue(any("slow run" in f for f in flags))
        self.assertTrue(any("few articles" in f for f in flags))
        self.assertTrue(any("status error" in f for f in flags))


class RunbookTests(unittest.TestCase):
    DOC = """# Troubleshooting
## Ollama box: wake, sleep and availability
### First check for ANY "Ollama is unreachable" report
Read /root/ollama_probe.log first.
## Search (Meilisearch)
### Full rebuild
Run as its own process.
- **Restarting containers from the admin UI:** use the proxy.
"""

    def test_split_includes_bold_bullets(self):
        titles = [s["title"] for s in v.split_sections(self.DOC, "T.md")]
        self.assertIn('First check for ANY "Ollama is unreachable" report', titles)
        self.assertIn("Restarting containers from the admin UI", titles)

    def test_search_ranks_title_matches(self):
        hits = v.search_sections(v.split_sections(self.DOC, "T.md"), "ollama unreachable")
        self.assertIn("unreachable", hits[0]["title"])
        self.assertEqual(v.search_sections(v.split_sections(self.DOC, "T.md"), "the a of"), [])


class RedactionTests(unittest.TestCase):
    def test_env_values_and_inline_secrets(self):
        env = {"NEWS_API_KEY": "abcdef123456", "MCP_TOKEN": "tok_0123456789abcdef", "OLLAMA_HOST": "http://x:11434",
               "SHORT_KEY": "abc"}
        secrets = v.secret_values(env)
        self.assertNotIn("http://x:11434", secrets)
        self.assertNotIn("abc", secrets)
        text = ('key abcdef123456 url https://n.org/v2?apiKey=ZZZ999&q=1 auth Bearer tok_0123456789abcdef '
                'db postgresql://user:hunter22@postgres:5432/x')
        out = v.redact(text, secrets)
        for leaked in ("abcdef123456", "ZZZ999", "tok_0123456789abcdef", "hunter22"):
            self.assertNotIn(leaked, out)
        self.assertIn("postgresql://user:***@postgres", out)
        self.assertIn("apiKey=***&q=1", out)


if __name__ == "__main__":
    unittest.main()
