"""Test PartialDownloadError is raised when kline download retries are exhausted."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from bt_api_py.bt_api import DOWNLOAD_MAX_RETRIES, BtApi
from bt_api_py.exceptions import PartialDownloadError, RequestError


class TestKlineDownloadRetryExhaustion:
    """Verify that _download_kline_by_range raises PartialDownloadError on retry exhaustion."""

    def test_retry_exhaustion_raises_partial_error(self) -> None:
        """When every batch download fails, retry exhaustion must raise PartialDownloadError."""
        api = BtApi(None, debug=False)

        begin_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

        with patch.object(
            api, "_download_single_batch", side_effect=RequestError("mock network error")
        ), patch("time.sleep", return_value=None), pytest.raises(PartialDownloadError) as exc_info:
            api._download_kline_by_range(
                feed=None,
                exchange_name="TEST___SPOT",
                symbol="BTCUSDT",
                period="1m",
                begin_time=begin_time,
                stop_time=None,
                extra_data=None,
            )

        msg = str(exc_info.value).lower()
        assert "partial" in msg or "incomplete" in msg or "max retries" in msg
        assert exc_info.value.downloaded_intervals == []

    def test_partial_download_with_some_success_then_exhaustion(self) -> None:
        """When some batches succeed then retries exhaust, intervals should be recorded."""
        api = BtApi(None, debug=False)

        begin_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

        # First batch succeeds, advancing begin_time by 1 minute
        advanced_time = begin_time + timedelta(minutes=1)

        # Build a side_effect: 1 success, then many failures
        side_effects = [advanced_time] + [RequestError("mock error")] * (DOWNLOAD_MAX_RETRIES + 1)

        with patch.object(
            api, "_download_single_batch", side_effect=side_effects
        ), patch("time.sleep", return_value=None), pytest.raises(PartialDownloadError) as exc_info:
            api._download_kline_by_range(
                feed=None,
                exchange_name="TEST___SPOT",
                symbol="BTCUSDT",
                period="1m",
                begin_time=begin_time,
                stop_time=None,
                extra_data=None,
            )

        intervals = exc_info.value.downloaded_intervals
        assert len(intervals) == 1
        assert intervals[0][0] == begin_time
        assert intervals[0][1] == advanced_time