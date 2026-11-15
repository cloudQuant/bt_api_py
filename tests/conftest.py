"""
Pytest configuration and fixtures for bt_api_py tests.

This file is auto-discovered by pytest and provides:
- Common fixtures for all tests
- Import of test data fixtures from conftest_test_data.py
- Shared test utilities and hooks
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Sibling test modules are imported by their bare module name in several suites
# (for example ``import test_ctp_execution_owner``), and some shared fixtures live
# in the owning SDK submodule's ``tests`` folder.  ``--import-mode=importlib`` no
# longer puts a test file's directory on ``sys.path`` the way ``prepend`` did, so
# keep those directories importable without renaming the modules.
_TESTS_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_ROOT.parent
_BARE_IMPORT_DIRS = (
    _TESTS_ROOT,
    _TESTS_ROOT / "unit",
    _TESTS_ROOT / "integration",
    _REPO_ROOT / "bt_api" / "bt_api_gateway" / "tests",
    _REPO_ROOT / "bt_api" / "bt_api_execution" / "tests",
    _REPO_ROOT / "bt_api" / "bt_api_monitor" / "tests",
    _REPO_ROOT / "bt_api" / "bt_api_ctp" / "tests",
)
for _import_dir in _BARE_IMPORT_DIRS:
    if _import_dir.is_dir() and str(_import_dir) not in sys.path:
        sys.path.append(str(_import_dir))

pytest_plugins = ["tests.conftest_test_data"]


@pytest.fixture(autouse=True)
def _isolated_execution_ledger_registry(monkeypatch, tmp_path):
    """Keep account-level execution leases isolated while preserving each test's sharing."""
    monkeypatch.setattr(
        "bt_api_py._execution_session._ledger_registry_root",
        lambda: tmp_path / "execution-ledgers",
    )
