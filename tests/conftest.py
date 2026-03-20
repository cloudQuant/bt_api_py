"""
Pytest configuration and fixtures for bt_api_py tests.

This file is auto-discovered by pytest and provides:
- Common fixtures for all tests
- Import of test data fixtures from conftest_test_data.py
- Shared test utilities and hooks
"""

pytest_plugins = ["conftest_test_data"]
