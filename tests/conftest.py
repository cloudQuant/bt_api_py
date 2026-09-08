"""
Pytest configuration and fixtures for bt_api_py tests.

This file is auto-discovered by pytest and provides:
- Common fixtures for all tests
- Import of test data fixtures from conftest_test_data.py
- Shared test utilities and hooks
"""

from __future__ import annotations

import pytest

pytest_plugins = ["tests.conftest_test_data"]


@pytest.fixture(autouse=True)
def _isolated_execution_ledger_registry(monkeypatch, tmp_path):
    """Keep account-level execution leases isolated while preserving each test's sharing."""
    monkeypatch.setattr(
        "bt_api_py._execution_session._ledger_registry_root",
        lambda: tmp_path / "execution-ledgers",
    )
