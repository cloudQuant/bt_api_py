"""Download Binance spot history bars without import-time side effects."""

from __future__ import annotations

import random
import time
from pathlib import Path

import pandas as pd
import requests

from bt_api_py.functions.utils import read_yaml_file

OUTPUT_DIR = Path("binance_spot_history_bar_data")
BAR_PERIOD = "15m"
BAR_LIMIT = 1500
DEFAULT_START_TIME = "2019-12-31 00:00:00"
DEFAULT_END_TIME = "2025-03-08 00:00:00"


def get_spot_symbol_list() -> list[str]:
    """Fetch the Binance spot symbol universe."""
    response = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=30)
    result = response.json()
    return [item["symbol"] for item in result["symbols"]]


def build_spot_exchange_params() -> dict[str, dict[str, str]]:
    """Build exchange configuration for spot bar downloads."""
    account_config_data = read_yaml_file("account_config.yaml")
    return {
        "OKX___SPOT": {
            "public_key": account_config_data["okx"]["public_key"],
            "private_key": account_config_data["okx"]["private_key"],
            "passphrase": account_config_data["okx"]["passphrase"],
        },
        "BINANCE___SPOT": {
            "public_key": account_config_data["binance"]["public_key"],
            "private_key": account_config_data["binance"]["private_key"],
        },
    }


def download_spot_history_bar_from_binance(
    *,
    output_dir: Path = OUTPUT_DIR,
    sleep_seconds: float = 30,
    shuffle_symbols: bool = True,
    start_time: str = DEFAULT_START_TIME,
    end_time: str = DEFAULT_END_TIME,
) -> list[str]:
    """Download Binance spot bars and return the list of newly written symbols."""
    from backtrader.stores.cryptostore import CryptoStore

    crypto_store = CryptoStore(build_spot_exchange_params(), debug=True)
    symbol_list = get_spot_symbol_list()
    if shuffle_symbols:
        random.shuffle(symbol_list)

    output_dir.mkdir(exist_ok=True)
    existing_files = {path.name for path in output_dir.iterdir() if path.is_file()}
    downloaded_symbols: list[str] = []

    for symbol in symbol_list:
        file_name = f"spot_history_bar_{symbol}.csv"
        if file_name in existing_files:
            continue

        bar_data_list = crypto_store.download_history_bars(
            f"BINANCE___SPOT___{symbol}",
            BAR_PERIOD,
            BAR_LIMIT,
            start_time,
            end_time,
        )
        if not bar_data_list:
            continue

        normalized_rows = [bar_data.get_all_data() for bar_data in bar_data_list]
        data = pd.DataFrame(normalized_rows)
        if data.empty:
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            continue

        data.to_csv(output_dir / file_name, index=False)
        downloaded_symbols.append(symbol)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return downloaded_symbols


def _main() -> None:
    downloaded = download_spot_history_bar_from_binance()
    print(f"Downloaded {len(downloaded)} Binance spot history bar files.")


if __name__ == "__main__":
    _main()
