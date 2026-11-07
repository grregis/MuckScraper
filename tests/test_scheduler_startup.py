import unittest
from datetime import datetime, timezone
from unittest.mock import patch

try:
    from news_fetcher import scheduler
except ModuleNotFoundError as exc:
    scheduler = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class SchedulerStartupTests(unittest.TestCase):
    # The schedule these assertions are written against. The real hours became
    # DB-backed and admin-editable with the PipelineSchedule model, so reading
    # them here would need a Flask app context AND make every assertion depend
    # on whatever an admin last saved in /admin/pipeline-schedule. Pinning them
    # keeps these as what they have always been: tests of the scheduling
    # arithmetic (timezone handling, day rollover, missed-slot detection),
    # not of the stored configuration.
    SCHEDULED_HOURS = [7, 12, 17, 22]
    FULL_PIPELINE_HOURS = [7, 17]

    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    def setUp(self):
        for name, hours in (
            ("_scheduled_hours", self.SCHEDULED_HOURS),
            ("_full_pipeline_hours", self.FULL_PIPELINE_HOURS),
        ):
            patcher = patch.object(scheduler, name, return_value=list(hours))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_startup_fetch_runs_when_scheduled_slot_was_missed(self):
        now = datetime(2026, 5, 14, 16, 30, tzinfo=timezone.utc)  # 12:30 ET
        last_fetch = datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc)  # 11:00 ET

        self.assertTrue(scheduler.should_fetch_now(now=now, last_fetch=last_fetch))

    def test_startup_fetch_waits_for_next_slot_when_none_missed(self):
        now = datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc)  # 11:30 ET
        last_fetch = datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc)  # 11:00 ET

        self.assertFalse(scheduler.should_fetch_now(now=now, last_fetch=last_fetch))

    def test_latest_scheduled_run_before_uses_timezone_schedule(self):
        now = datetime(2026, 5, 14, 3, 30, tzinfo=timezone.utc)  # 23:30 ET on 5/13
        latest = scheduler._latest_scheduled_run_before(now=now)

        self.assertEqual(latest, datetime(2026, 5, 14, 2, 0, tzinfo=timezone.utc))  # 22:00 ET on 5/13

    def test_full_pipeline_schedule_uses_separate_hours(self):
        morning_run = datetime(2026, 5, 14, 11, 0, tzinfo=timezone.utc)  # 07:00 ET
        noon_run = datetime(2026, 5, 14, 16, 0, tzinfo=timezone.utc)  # 12:00 ET

        self.assertTrue(scheduler.should_run_full_pipeline(now=morning_run))
        self.assertFalse(scheduler.should_run_full_pipeline(now=noon_run))

    def test_startup_full_pipeline_runs_when_morning_full_slot_was_missed(self):
        now = datetime(2026, 5, 14, 16, 30, tzinfo=timezone.utc)  # 12:30 ET
        last_fetch = datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc)  # 06:00 ET

        self.assertTrue(scheduler.should_run_full_pipeline(now=now, last_fetch=last_fetch))


if __name__ == "__main__":
    unittest.main()
