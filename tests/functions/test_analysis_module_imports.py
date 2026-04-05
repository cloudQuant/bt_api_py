"""Regression tests for analysis modules that previously executed at import time."""

from __future__ import annotations

import importlib


def test_analysis_deals_import_is_safe() -> None:
    module = importlib.import_module("bt_api_py.functions.analysis_deals")

    assert hasattr(module, "analyze_deals")
    assert hasattr(module, "load_trade_frames")


def test_analysis_log_import_is_safe() -> None:
    module = importlib.import_module("bt_api_py.functions.analysis_log")

    assert hasattr(module, "build_duration_series")
    assert hasattr(module, "render_report")
