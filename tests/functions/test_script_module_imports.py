"""Regression tests for utility modules that should be safe to import."""

from __future__ import annotations

import importlib


def test_calculate_time_import_is_safe() -> None:
    module = importlib.import_module("bt_api_py.functions.calculate_time")

    assert hasattr(module, "get_utc_time")
    assert hasattr(module, "datetime2timestamp")


def test_timer_event_import_is_safe() -> None:
    module = importlib.import_module("bt_api_py.functions.timer_event")

    assert hasattr(module, "run_on_timer")


def test_async_modules_import_is_safe() -> None:
    async_base = importlib.import_module("bt_api_py.functions.async_base")
    async_send_message = importlib.import_module("bt_api_py.functions.async_send_message")

    assert hasattr(async_base, "AsyncBase")
    assert hasattr(async_send_message, "FeishuManagerAsync")
    assert hasattr(async_send_message, "EmailManagerAsync")
