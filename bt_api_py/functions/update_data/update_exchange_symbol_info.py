from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from bt_api_py.containers.symbols.binance_symbol import BinanceSpotSymbolData, BinanceSwapSymbolData
from bt_api_py.functions.utils import get_package_path


def _get_bt_api_root() -> Path:
    package_path = get_package_path("bt_api_py")
    if package_path is None:
        raise RuntimeError("Package path for 'bt_api_py' is not available")
    return Path(package_path)


def _dump_pickle(data: dict[str, Any], output_path: Path) -> None:
    with output_path.open("wb") as file_obj:
        pickle.dump(data, file_obj)


def update_symbol_info(exchange_name: str, asset_type: str) -> None:
    if exchange_name == "BINANCE" and asset_type == "SPOT":
        update_binance_spot_symbol_info()
    if exchange_name == "BINANCE" and asset_type == "SWAP":
        update_binance_swap_symbol_info()


def update_okex_symbol_info(instrument_type: str = "SWAP") -> pd.DataFrame:
    """Download OKX instrument metadata and persist it to ``configs``."""
    url = "https://www.okx.com/api/v5/public/instruments?instType=" + instrument_type
    page = requests.get(url, timeout=30)
    data = page.json()["data"]
    df = pd.DataFrame(data)
    output_path = _get_bt_api_root() / "configs" / f"okex_{instrument_type}_symbol.csv"
    df.to_csv(output_path, index=False)
    return df


def update_binance_swap_symbol_info() -> dict[str, BinanceSwapSymbolData]:
    """Download and persist Binance swap symbol metadata."""
    res = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30)
    result = res.json()
    data: dict[str, BinanceSwapSymbolData] = {}
    for symbol_info in result["symbols"]:
        symbol_name = symbol_info["symbol"]
        data[symbol_name] = BinanceSwapSymbolData(symbol_info, True)
    _dump_pickle(data, _get_bt_api_root() / "configs" / "binance_swap_symbol_info.pkl")
    return data


def update_binance_spot_symbol_info() -> dict[str, BinanceSpotSymbolData]:
    """Download and persist Binance spot symbol metadata."""
    res = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=30)
    result = res.json()
    data: dict[str, BinanceSpotSymbolData] = {}
    for symbol_info in result["symbols"]:
        symbol_name = symbol_info["symbol"]
        data[symbol_name] = BinanceSpotSymbolData(symbol_info, True)
    _dump_pickle(data, _get_bt_api_root() / "configs" / "binance_spot_symbol_info.pkl")
    return data


def _main() -> None:
    update_binance_spot_symbol_info()


if __name__ == "__main__":
    _main()
