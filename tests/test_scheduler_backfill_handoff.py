"""End of a run: shut the Ollama box down, or continue a backfill the run
stopped ("continue after runs", 2026-11-19). scheduler._shut_down_or_continue_backfill."""
from unittest.mock import patch

from news_fetcher import scheduler


def _end_of_run(resume_wanted, resume_result=(True, "Requested")):
    events = []
    with patch.object(scheduler.backfill, "resume_wanted", return_value=resume_wanted), \
         patch.object(scheduler.backfill, "request_resume",
                      side_effect=lambda: events.append("resume") or resume_result), \
         patch.object(scheduler, "_notify_n8n", side_effect=lambda: events.append("shutdown")):
        scheduler._shut_down_or_continue_backfill(lambda: events.append("idle"))
    return events


def test_no_backfill_to_continue_shuts_the_box_down():
    # Switch off, or on with no backfill stopped by the run: the normal end.
    assert _end_of_run(False) == ["shutdown", "idle"]


def test_backfill_to_continue_keeps_the_box_on_and_resumes_after_idle():
    assert _end_of_run(True) == ["idle", "resume"]


def test_resume_refused_falls_back_to_shutdown():
    assert _end_of_run(True, (False, "A backfill is already running.")) == ["idle", "resume", "shutdown"]


def test_check_error_falls_back_to_shutdown():
    events = []
    with patch.object(scheduler.backfill, "resume_wanted", side_effect=RuntimeError("db down")), \
         patch.object(scheduler, "_notify_n8n", side_effect=lambda: events.append("shutdown")):
        scheduler._shut_down_or_continue_backfill(lambda: events.append("idle"))
    assert events == ["shutdown", "idle"]
