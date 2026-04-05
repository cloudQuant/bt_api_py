"""Tests for CTP API modules.

Note: CTP is a C++ extension module.
"""

from __future__ import annotations

import pytest


class TestCtpApi:
    """Tests for CTP API modules."""

    def test_ctp_md_api_import(self):
        """Test MD API can be imported."""
        try:
            from bt_api_py.ctp import ctp_md_api

            assert ctp_md_api is not None
        except ImportError:
            pytest.skip("CTP extension not available")

    def test_ctp_trader_api_import(self):
        """Test trader API can be imported."""
        try:
            from bt_api_py.ctp import ctp_trader_api

            assert ctp_trader_api is not None
        except ImportError:
            pytest.skip("CTP extension not available")
