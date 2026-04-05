"""Regression tests for update_data modules that should be safe to import."""

from __future__ import annotations

import importlib


def test_binance_spot_history_script_import_is_safe() -> None:
    module = importlib.import_module(
        "bt_api_py.functions.update_data.download_spot_history_bar_from_binance"
    )

    assert hasattr(module, "get_spot_symbol_list")
    assert hasattr(module, "download_spot_history_bar_from_binance")


def test_binance_swap_history_script_import_is_safe() -> None:
    module = importlib.import_module(
        "bt_api_py.functions.update_data.download_swap_history_bar_from_binance"
    )

    assert hasattr(module, "get_swap_symbol_list")
    assert hasattr(module, "download_swap_history_bar_from_binance")


def test_binance_funding_rate_script_import_is_safe() -> None:
    module = importlib.import_module(
        "bt_api_py.functions.update_data.download_funding_rate_from_binance"
    )

    assert hasattr(module, "get_perpetual_symbol_list")
    assert hasattr(module, "download_funding_rate_from_binance")


def test_okx_spot_history_script_import_is_safe() -> None:
    module = importlib.import_module(
        "bt_api_py.functions.update_data.download_spot_history_bar_from_okx"
    )

    assert hasattr(module, "get_spot_symbol_list")
    assert hasattr(module, "download_spot_history_bar_from_okx")


def test_okex_bar_download_script_import_is_safe() -> None:
    module = importlib.import_module("bt_api_py.functions.update_data.download_bars_from_okex")

    assert hasattr(module, "download_okex_bars")
    assert hasattr(module, "download_all_bars")
