import unittest
from unittest.mock import patch

try:
    from news_fetcher import scheduler
except ModuleNotFoundError as exc:
    scheduler = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class SchedulerMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    def test_build_scrape_outcome_history_entry_captures_run_and_headline_metrics(self):
        run_metrics = {
            "status": "ok",
            "started_at": "2026-05-14T02:00:00",
            "finished_at": "2026-05-14T04:11:45",
            "totals": {
                "stored": 141,
                "input_articles": 412,
                "scrape_statuses": {"success": 113, "fallback": 16, "blocked": 10},
            },
        }
        headline_site_metrics = {
            "edition": {"id": 88, "date": "2026-05-13", "edition_type": "night"},
            "scrape": {
                "articles_by_status": {"success": 64, "fallback": 9, "blocked": 18},
                "readable_articles": 73,
                "fully_read_articles": 64,
                "blocked_articles": 18,
            },
        }

        entry = scheduler._build_scrape_outcome_history_entry(run_metrics, headline_site_metrics)

        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["duration_seconds"], 7905)
        self.assertEqual(entry["edition"]["id"], 88)
        self.assertEqual(entry["run_scrape_statuses"]["success"], 113)
        self.assertEqual(entry["headline_scrape"]["blocked"], 18)
        self.assertEqual(entry["headline_readable_articles"], 73)

    def test_history_entry_carries_quality_counters_and_none_for_skipped_phases(self):
        base = {
            "status": "ok", "started_at": "2026-09-29T02:00:00", "finished_at": "2026-09-29T03:00:00",
            "totals": {"stored": 1, "input_articles": 2},
        }
        skipped = {"status": "skipped", "reason": "fetch_only_run"}

        fetch_only = dict(base, steps={
            "headline_generation": skipped, "review_ambiguous_grouping_matches": skipped,
            "process_current_edition": skipped, "publish_edition": skipped,
        })
        q = scheduler._build_scrape_outcome_history_entry(fetch_only, {})["quality"]
        self.assertEqual(q, {"headline_generation": None, "grouping_review": None,
                             "edition_content": None, "edition_publish": None})

        full = dict(base, steps={
            "headline_generation": {"status": "ok", "candidates": 12, "generated": 11, "failed": 1},
            "review_ambiguous_grouping_matches": {"status": "ok", "reviewed": 30, "reassigned": 0},
            "process_current_edition": {"status": "processed", "story_summaries_generated": 0,
                                        "deep_reports_generated": 3, "child_article_summaries_generated": 9,
                                        "stale_stories_reset": 2},
            "publish_edition": {"status": "error", "reason": "boom"},
        })
        q = scheduler._build_scrape_outcome_history_entry(full, {})["quality"]
        self.assertEqual(q["headline_generation"], {"candidates": 12, "generated": 11, "failed": 1})
        self.assertEqual(q["grouping_review"]["reassigned"], 0)
        self.assertEqual(q["edition_content"]["story_summaries_generated"], 0)
        self.assertIsNone(q["edition_publish"])

    def test_history_entry_without_steps_does_not_raise(self):
        entry = scheduler._build_scrape_outcome_history_entry({"totals": {}}, {})
        self.assertTrue(all(v is None for v in entry["quality"].values()))

    def test_append_scrape_outcome_history_trims_to_max_runs(self):
        initial_history = [
            {"recorded_at": "2026-05-10T00:00:00"},
            {"recorded_at": "2026-05-11T00:00:00"},
        ]
        run_metrics = {
            "status": "ok",
            "started_at": "2026-05-12T00:00:00",
            "finished_at": "2026-05-12T01:00:00",
            "totals": {"stored": 1, "input_articles": 2, "scrape_statuses": {"success": 1}},
        }
        headline_site_metrics = {
            "edition": {"id": 1, "date": "2026-05-12", "edition_type": "morning"},
            "scrape": {"articles_by_status": {"success": 1}},
        }
        saved = {}

        def fake_save(key, value):
            saved["key"] = key
            saved["value"] = value

        with patch.object(scheduler, "_load_json_setting", return_value=initial_history), \
             patch.object(scheduler, "_save_json_setting", side_effect=fake_save):
            history = scheduler.append_scrape_outcome_history(run_metrics, headline_site_metrics, max_runs=2)

        self.assertEqual(saved["key"], scheduler.SCRAPE_OUTCOME_HISTORY_KEY)
        self.assertEqual(len(saved["value"]), 2)
        self.assertEqual(saved["value"][0]["recorded_at"], "2026-05-11T00:00:00")
        self.assertEqual(saved["value"][-1]["edition"]["edition_type"], "morning")
        self.assertEqual(history, saved["value"])

    def test_build_fetch_report_exposes_top_level_compatibility_fields(self):
        run_metrics = {
            "status": "ok",
            "started_at": "2026-05-14T02:00:00",
            "finished_at": "2026-05-14T04:11:45",
            "totals": {
                "stored": 141,
                "input_articles": 412,
                "new_outlets": 5,
                "stories_touched": 99,
                "scrape_statuses": {"success": 113, "fallback": 16, "blocked": 10},
            },
        }
        headline_site_metrics = {
            "edition": {"id": 88, "date": "2026-05-13", "edition_type": "night"},
            "scrape": {
                "articles_by_status": {"success": 64, "fallback": 9, "blocked": 18},
                "readable_articles": 73,
                "fully_read_articles": 64,
                "blocked_articles": 18,
            },
        }
        ollama_state = {"up_at_start": True, "up_at_end": True, "went_down_during_run": False, "checks": []}

        report = scheduler._build_fetch_report(run_metrics, headline_site_metrics, ollama_state)

        self.assertEqual(report["stored_articles"], 141)
        self.assertEqual(report["input_articles"], 412)
        self.assertEqual(report["new_outlets"], 5)
        self.assertEqual(report["stories_touched"], 99)
        self.assertEqual(report["headline_scrape"]["blocked"], 18)
        self.assertEqual(report["headline_readable_articles"], 73)
        self.assertEqual(report["edition"]["edition_type"], "night")


if __name__ == "__main__":
    unittest.main()
