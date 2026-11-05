import logging
from unittest.mock import MagicMock, patch
import pytest

from google.genai.errors import ClientError
from news_fetcher import llm_client
from news_fetcher.llm_client import _generate_text_gemini


@pytest.fixture(autouse=True)
def gemini_env(monkeypatch):
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "test-api-key")
    monkeypatch.setattr(llm_client, "GEMINI_MODEL", "gemini-2.5-flash-lite")
    monkeypatch.setattr(llm_client, "GEMINI_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(llm_client, "_gemini_daily_quota_logged", False)
    monkeypatch.setattr(llm_client, "_last_gemini_request_time", 0.0)


def test_gemini_per_minute_429_retried_after_retry_delay(monkeypatch):
    """A per-minute 429 quota exhaustion should wait the specified retryDelay and retry."""
    mock_client = MagicMock()
    monkeypatch.setattr(llm_client, "_get_gemini_client", lambda: mock_client)

    error_response = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exceeded",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {"quotaId": "GenerateRequestsPerMinutePerProject"}
                    ],
                },
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "12s",
                },
            ],
        }
    }
    per_minute_err = ClientError(429, error_response)

    mock_success = MagicMock()
    mock_success.text = "Generated headline text"

    mock_client.models.generate_content.side_effect = [per_minute_err, mock_success]

    with patch("time.sleep") as mock_sleep:
        result = _generate_text_gemini("Test prompt", timeout=10)

    assert result == "Generated headline text"
    assert mock_client.models.generate_content.call_count == 2
    mock_sleep.assert_called_once_with(12.0)


def test_gemini_per_day_429_returns_none_without_sleeping(monkeypatch):
    """A per-day quota exhaustion must fail fast (return None) without sleeping."""
    mock_client = MagicMock()
    monkeypatch.setattr(llm_client, "_get_gemini_client", lambda: mock_client)

    error_response = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Resource has been exhausted",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {"quotaId": "GenerateRequestsPerDayPerProject"}
                    ],
                }
            ],
        }
    }
    per_day_err = ClientError(429, error_response)
    mock_client.models.generate_content.side_effect = per_day_err

    with patch("time.sleep") as mock_sleep:
        result = _generate_text_gemini("Test prompt", timeout=10)

    assert result is None
    assert mock_client.models.generate_content.call_count == 1
    mock_sleep.assert_not_called()

    # Second call should also fail fast and not log warning again
    with patch("time.sleep") as mock_sleep2:
        result2 = _generate_text_gemini("Second prompt", timeout=10)

    assert result2 is None
    assert mock_client.models.generate_content.call_count == 2
    mock_sleep2.assert_not_called()


def test_gemini_non_429_error_not_retried(monkeypatch):
    """Non-429 exceptions (e.g. ValueError or 400 Bad Request) must not be retried."""
    mock_client = MagicMock()
    monkeypatch.setattr(llm_client, "_get_gemini_client", lambda: mock_client)
    mock_client.models.generate_content.side_effect = ValueError("Invalid prompt parameters")

    with patch("time.sleep") as mock_sleep:
        result = _generate_text_gemini("Test prompt", timeout=10)

    assert result is None
    assert mock_client.models.generate_content.call_count == 1
    mock_sleep.assert_not_called()


def test_gemini_min_interval_pacing(monkeypatch):
    """When GEMINI_MIN_INTERVAL_SECONDS is configured, Gemini calls are paced."""
    mock_client = MagicMock()
    monkeypatch.setattr(llm_client, "_get_gemini_client", lambda: mock_client)
    monkeypatch.setattr(llm_client, "GEMINI_MIN_INTERVAL_SECONDS", 2.0)

    mock_response = MagicMock()
    mock_response.text = "Paced result"
    mock_client.models.generate_content.return_value = mock_response

    # Pretend last call happened 0.5s ago
    with patch("time.time", side_effect=[100.5, 102.0]):
        monkeypatch.setattr(llm_client, "_last_gemini_request_time", 100.0)
        with patch("time.sleep") as mock_sleep:
            result = _generate_text_gemini("Test prompt", timeout=10)

    assert result == "Paced result"
    mock_sleep.assert_called_once_with(1.5)


def test_gemini_daily_quota_logged_resets_on_success(monkeypatch, caplog):
    """A successful call to Gemini resets _gemini_daily_quota_logged so subsequent exhaustion logs again."""
    mock_client = MagicMock()
    monkeypatch.setattr(llm_client, "_get_gemini_client", lambda: mock_client)

    error_response = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Resource has been exhausted",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {"quotaId": "GenerateRequestsPerDayPerProject"}
                    ],
                }
            ],
        }
    }
    per_day_err = ClientError(429, error_response)
    mock_success = MagicMock()
    mock_success.text = "Success response"

    # Sequence: 1. Daily quota fail -> 2. Daily quota fail (suppressed log) -> 3. Success (resets) -> 4. Daily quota fail (logged again)
    mock_client.models.generate_content.side_effect = [
        per_day_err,
        per_day_err,
        mock_success,
        per_day_err,
    ]

    with caplog.at_level(logging.WARNING, logger="news_fetcher.llm_client"):
        # 1. First failure sets flag to True and logs warning
        res1 = _generate_text_gemini("Prompt 1", timeout=10)
        assert res1 is None
        assert llm_client._gemini_daily_quota_logged is True
        assert caplog.text.count("Gemini daily quota exhausted") == 1

        # 2. Second failure does not log again
        res2 = _generate_text_gemini("Prompt 2", timeout=10)
        assert res2 is None
        assert caplog.text.count("Gemini daily quota exhausted") == 1

        # 3. Successful call resets flag to False
        res3 = _generate_text_gemini("Prompt 3", timeout=10)
        assert res3 == "Success response"
        assert llm_client._gemini_daily_quota_logged is False

        # 4. Subsequent failure logs warning again and re-sets flag
        res4 = _generate_text_gemini("Prompt 4", timeout=10)
        assert res4 is None
        assert llm_client._gemini_daily_quota_logged is True
        assert caplog.text.count("Gemini daily quota exhausted") == 2

