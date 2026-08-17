"""历史 K 线下载（含周期解析与重试）。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from bt_api_base._compat import UTC
from bt_api_base.exceptions import (
    DataParseError,
    RequestError,
    RequestFailedError,
    RequestTimeoutError,
)

from bt_api_py.exceptions import PartialDownloadError

DOWNLOAD_RETRY_DELAY_SEC = 3
DOWNLOAD_MAX_RETRIES = 10
DOWNLOAD_RETRY_BACKOFF_FACTOR = 2.0
DOWNLOAD_RETRY_MAX_DELAY_SEC = 60

KLINE_PERIOD_DELTAS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "3m": timedelta(minutes=3),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1H": timedelta(hours=1),
    "1D": timedelta(days=1),
}


def _calculate_time_delta(period: str) -> timedelta:
    if period in KLINE_PERIOD_DELTAS:
        return KLINE_PERIOD_DELTAS[period]
    raise DataParseError(detail=f"Unsupported period: {period}")


def _parse_time(input_time: str | datetime | None) -> datetime | None:
    if isinstance(input_time, str):
        try:
            parsed = datetime.fromisoformat(input_time)
        except ValueError as e:
            raise DataParseError(detail=f"Invalid ISO time format: {input_time}") from e
    elif isinstance(input_time, datetime):
        parsed = input_time
    elif input_time is None:
        return None
    else:
        raise DataParseError(detail=f"Unsupported time format: {type(input_time)}")
    # 统一语义:所有 naive 输入按 UTC 解释;带 tz 输入保持原 tz 转 UTC 返回。
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class DataDownloaderMixin:
    """历史 K 线下载方法（供 BtApi 混入）。"""

    def download_history_bars(
        self,
        exchange_name: str,
        symbol: str,
        period: str,
        count: int = 100,
        start_time: str | datetime | None = None,
        end_time: str | datetime | None = None,
        extra_data: Any = None,
    ) -> None:
        begin_time = _parse_time(start_time)
        stop_time = _parse_time(end_time)
        feed = self._get_feed(exchange_name)

        if begin_time is None:
            self._download_kline_by_count(feed, exchange_name, symbol, period, count, extra_data)
            return

        self._download_kline_by_range(
            feed, exchange_name, symbol, period, begin_time, stop_time, extra_data
        )

    def _download_kline_by_count(
        self,
        feed: Any,
        exchange_name: str,
        symbol: str,
        period: str,
        count: int,
        extra_data: Any,
    ) -> None:
        data = feed.get_kline(symbol, period, count, extra_data=extra_data)
        self.push_bar_data_to_queue(exchange_name, data)
        self.log(f"download completely: {symbol}, new {count} bar")

    def _download_kline_by_range(
        self,
        feed: Any,
        exchange_name: str,
        symbol: str,
        period: str,
        begin_time: datetime,
        stop_time: datetime | None,
        extra_data: Any,
    ) -> None:
        if stop_time is None:
            stop_time = self._calculate_aligned_stop_time(period)

        retry_count = 0
        current_delay = DOWNLOAD_RETRY_DELAY_SEC
        downloaded_intervals: list[tuple[datetime, datetime]] = []
        while begin_time < stop_time:
            if retry_count >= DOWNLOAD_MAX_RETRIES:
                msg = (
                    f"kline download incomplete for {symbol} after "
                    f"{DOWNLOAD_MAX_RETRIES} retries — {len(downloaded_intervals)} "
                    f"interval(s) downloaded before exhaustion"
                )
                self.log(msg, level="error")
                raise PartialDownloadError(
                    msg,
                    downloaded_intervals=downloaded_intervals,
                )
            try:
                batch_start = begin_time
                begin_time = self._download_single_batch(
                    feed, exchange_name, symbol, period, begin_time, stop_time, extra_data
                )
                downloaded_intervals.append((batch_start, begin_time))
                retry_count = 0
                current_delay = DOWNLOAD_RETRY_DELAY_SEC
            except (
                RequestError,
                RequestTimeoutError,
                RequestFailedError,
                ValueError,
                KeyError,
            ) as e:
                retry_count += 1
                self.log(
                    f"download fail (attempt {retry_count}/{DOWNLOAD_MAX_RETRIES}), "
                    f"retry in {current_delay}s: {e}",
                    level="warning",
                )
                time.sleep(current_delay)
                current_delay = min(
                    current_delay * DOWNLOAD_RETRY_BACKOFF_FACTOR,
                    DOWNLOAD_RETRY_MAX_DELAY_SEC,
                )

        self.log(f"download all data completely: {symbol}, period: {period}")

    def _calculate_aligned_stop_time(self, period: str) -> datetime:
        now = datetime.now(UTC)
        period_seconds = int(_calculate_time_delta(period).total_seconds())
        return now - timedelta(seconds=now.timestamp() % period_seconds)

    def _download_single_batch(
        self,
        feed: Any,
        exchange_name: str,
        symbol: str,
        period: str,
        begin_time: datetime,
        stop_time: datetime,
        extra_data: Any,
    ) -> datetime:
        time_delta = _calculate_time_delta(period)
        current_end_time = min(begin_time + time_delta, stop_time)

        begin_stamp = int(1000.0 * begin_time.timestamp())
        end_stamp = int(1000.0 * current_end_time.timestamp())

        data = feed.get_kline(
            symbol, period, start_time=begin_stamp, end_time=end_stamp, extra_data=extra_data
        )
        self.push_bar_data_to_queue(exchange_name, data)
        self.log(
            f"download successfully: {symbol}, period: {period}, "
            f"begin: {begin_time}, end: {current_end_time}"
        )

        return current_end_time
