"""Download OKX historical bars without import-time side effects."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from time import sleep

import pandas as pd
import requests

PERIOD_WINDOW_MAP = {
    "1m": timedelta(hours=1),
    "3m": timedelta(hours=5),
    "5m": timedelta(hours=9),
    "15m": timedelta(hours=25),
    "30m": timedelta(hours=50),
    "1H": timedelta(hours=100),
    "1D": timedelta(hours=24 * 100),
    "1Dutc": timedelta(hours=24 * 100),
}


def get_okex_instrument_info(instrument_type: str = "SWAP") -> dict[str, object]:
    """Fetch OKX instrument metadata for the given instrument type."""
    url = f"https://www.okx.com/api/v5/public/instruments?instType={instrument_type}"
    return requests.get(url, timeout=30).json()


def _period_to_window(period: str) -> timedelta:
    if period not in PERIOD_WINDOW_MAP:
        raise ValueError(f"Unsupported period: {period}")
    return PERIOD_WINDOW_MAP[period]


def _normalize_bar_frame(raw_data: list[list[object]]) -> pd.DataFrame:
    data = pd.DataFrame(
        raw_data,
        columns=[
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "volCcy",
            "volCcyQuote",
            "status",
        ],
    )
    data["datetime"] = pd.to_datetime(data["datetime"], unit="ms")
    data = data[["datetime", "open", "high", "low", "close", "volume"]]
    return data.drop_duplicates("datetime")


def download_okex_bars(
    symbol: str,
    period: str,
    begin_time: str,
    end_time: str,
    dst: str | Path = "okex_btc-usdt.csv",
    *,
    request_sleep_seconds: float = 0.1,
    retry_sleep_seconds: float = 3,
) -> pd.DataFrame | None:
    """Download OKX bars to ``dst`` and return the merged DataFrame."""
    destination = Path(dst)
    result: list[pd.DataFrame] = []

    if destination.exists():
        last_df = pd.read_csv(destination)
        result = [last_df]
        last_end_time = str(list(last_df["datetime"])[-1])
        current_begin_time = datetime.fromisoformat(max(begin_time, last_end_time))
    else:
        current_begin_time = datetime.fromisoformat(begin_time)

    stop_time = datetime.fromisoformat(end_time)
    count = 0

    while True:
        try:
            current_end_time = current_begin_time + _period_to_window(period)
            begin_stamp = current_begin_time.timestamp() * 1000
            end_stamp = current_end_time.timestamp() * 1000
            url = (
                "https://www.okex.com/api/v5/market/history-candles"
                f"?instId={symbol}&bar={period}&after={int(end_stamp)}&before={int(begin_stamp)}"
            )
            response = requests.get(url, timeout=30).json()
            raw_rows = response.get("data", [])
            if not raw_rows:
                current_begin_time = current_end_time
                if current_end_time > stop_time:
                    break
                continue

            df = _normalize_bar_frame(raw_rows)
            result.append(df)
            count += 1
            current_begin_time = current_end_time
            sleep(request_sleep_seconds)
            if current_end_time > stop_time:
                break
            if count % 10 == 0:
                merged = pd.concat(result, axis=0)
                normalized = merged[["datetime", "open", "high", "low", "close", "volume"]]
                normalized = normalized.drop_duplicates("datetime")
                destination.parent.mkdir(parents=True, exist_ok=True)
                normalized.to_csv(destination, index=False)
        except Exception:
            sleep(retry_sleep_seconds)

    if not result:
        return None

    merged = pd.concat(result, axis=0)
    normalized = merged[["datetime", "open", "high", "low", "close", "volume"]]
    normalized = normalized.drop_duplicates("datetime")
    destination.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(destination, index=False)
    return normalized


def download_all_bars(
    period: str = "1H",
    begin_time: str = "2020-01-01 00:00:00",
    end_time: str = "2023-10-31 00:00:00",
    *,
    output_root: Path = Path("datas"),
) -> list[str]:
    """Download all OKX swap bars for the given period and return processed symbols."""
    instrument_info_list = get_okex_instrument_info()
    symbol_list = [item["instId"] for item in instrument_info_list.get("data", [])]
    written_symbols: list[str] = []

    for symbol in symbol_list:
        dst = output_root / period / f"{symbol}.csv"
        data = download_okex_bars(symbol, period, begin_time, end_time, dst)
        if data is not None and not data.empty:
            written_symbols.append(symbol)

    return written_symbols


def _main() -> None:
    downloaded = download_all_bars(
        period="1D",
        begin_time="2020-01-01 00:00:00",
        end_time="2023-10-31 00:00:00",
    )
    print(f"Downloaded {len(downloaded)} OKX symbol files.")


if __name__ == "__main__":
    _main()
