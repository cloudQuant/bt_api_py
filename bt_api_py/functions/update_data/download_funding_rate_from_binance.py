"""Download Binance funding rate history without import-time side effects."""

from __future__ import annotations

import queue
import random
import time
from pathlib import Path

import pandas as pd
import requests

from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataSwap
from bt_api_py.feeds.live_binance_feed import BinanceRequestDataSwap
from bt_api_py.functions.utils import read_yaml_file

OUTPUT_DIR = Path("binance_funding_rate_data")
TIME_WINDOWS = [
    "2019-12-31 00:00:00.000",
    "2020-03-31 00:00:00.000",
    "2020-06-30 00:00:00.000",
    "2020-09-30 00:00:00.000",
    "2020-12-31 00:00:00.000",
    "2021-03-31 00:00:00.000",
    "2021-06-30 00:00:00.000",
    "2021-09-30 00:00:00.000",
    "2021-12-31 00:00:00.000",
    "2022-03-31 00:00:00.000",
    "2022-06-30 00:00:00.000",
    "2022-09-30 00:00:00.000",
    "2022-12-31 00:00:00.000",
    "2023-03-31 00:00:00.000",
    "2023-06-30 00:00:00.000",
    "2023-09-30 00:00:00.000",
    "2023-12-31 00:00:00.000",
    "2024-03-31 00:00:00.000",
    "2024-06-30 00:00:00.000",
    "2024-09-30 00:00:00.000",
    "2024-12-31 00:00:00.000",
    "2025-03-31 00:00:00.000",
]


def build_funding_rate_feed() -> BinanceRequestDataSwap:
    """Create the Binance swap request feed used for funding-rate history."""
    data_queue_: queue.Queue[object] = queue.Queue()
    config = read_yaml_file("account_config.yaml")
    kwargs_ = {
        "public_key": config["binance"]["public_key"],
        "private_key": config["binance"]["private_key"],
        "exchange_data": BinanceExchangeDataSwap(),
        "topics": {"tick": {"symbol": "BTC-USDT"}},
    }
    return BinanceRequestDataSwap(data_queue_, **kwargs_)


def get_perpetual_symbol_list() -> list[str]:
    """Fetch Binance perpetual contract symbols."""
    response = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30)
    result = response.json()
    return [item["symbol"] for item in result["symbols"] if item["contractType"] == "PERPETUAL"]


def _normalize_funding_rate_rows(
    funding_rate_data: object,
) -> list[list[object]]:
    result: list[list[object]] = []
    funding_rate_list = funding_rate_data.get_data()
    for item in funding_rate_list:
        item.init_data()
        result.append(
            [
                item.get_symbol_name(),
                item.get_current_funding_rate(),
                item.get_current_funding_time(),
            ]
        )
    return result


def download_funding_rate_from_binance(
    *,
    output_dir: Path = OUTPUT_DIR,
    sleep_between_windows: float = 1,
    sleep_between_symbols: float = 30,
    shuffle_symbols: bool = True,
) -> list[str]:
    """Download funding-rate CSVs and return the list of newly written symbols."""
    live_binance_swap_feed = build_funding_rate_feed()
    symbol_list = get_perpetual_symbol_list()
    if shuffle_symbols:
        random.shuffle(symbol_list)

    output_dir.mkdir(exist_ok=True)
    existing_files = {path.name for path in output_dir.iterdir() if path.is_file()}
    downloaded_symbols: list[str] = []

    for symbol in symbol_list:
        file_name = f"funding_rate_{symbol}.csv"
        if file_name in existing_files:
            continue

        funding_rate_data_list: list[pd.DataFrame] = []
        for index in range(len(TIME_WINDOWS) - 1):
            funding_rate_data = live_binance_swap_feed.get_history_funding_rate(
                symbol,
                start_time=TIME_WINDOWS[index],
                end_time=TIME_WINDOWS[index + 1],
                limit=1000,
            )
            result = _normalize_funding_rate_rows(funding_rate_data)
            df = pd.DataFrame(
                result,
                columns=["symbol", "current_funding_rate", "funding_rate_time"],
            )
            if not df.empty:
                funding_rate_data_list.append(df)
            if sleep_between_windows > 0:
                time.sleep(sleep_between_windows)

        if not funding_rate_data_list:
            continue

        data = pd.concat(funding_rate_data_list, axis=0)
        if data.empty:
            if sleep_between_symbols > 0:
                time.sleep(sleep_between_symbols)
            continue

        data.to_csv(output_dir / file_name, index=False)
        downloaded_symbols.append(symbol)
        if sleep_between_symbols > 0:
            time.sleep(sleep_between_symbols)

    return downloaded_symbols


def _main() -> None:
    downloaded = download_funding_rate_from_binance()
    print(f"Downloaded {len(downloaded)} Binance funding rate files.")


if __name__ == "__main__":
    _main()
