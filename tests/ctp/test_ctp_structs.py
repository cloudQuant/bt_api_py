"""Tests for CTP structs modules.

Note: CTP is a C++ extension module.
"""

from __future__ import annotations

import pytest


class TestCtpStructs:
    """Tests for CTP structs."""

    def test_ctp_structs_market_import(self):
        """Test market structs can be imported."""
        try:
            from bt_api_py.ctp import ctp_structs_market

            assert ctp_structs_market is not None
        except ImportError:
            pytest.skip("CTP extension not available")

    def test_ctp_structs_order_import(self):
        """Test order structs can be imported."""
        try:
            from bt_api_py.ctp import ctp_structs_order

            assert ctp_structs_order is not None
        except ImportError:
            pytest.skip("CTP extension not available")

    def test_ctp_structs_account_import(self):
        """Test account structs can be imported."""
        try:
            from bt_api_py.ctp import ctp_structs_account

            assert ctp_structs_account is not None
        except ImportError:
            pytest.skip("CTP extension not available")

    def test_ctp_structs_position_import(self):
        """Test position structs can be imported."""
        try:
            from bt_api_py.ctp import ctp_structs_position

            assert ctp_structs_position is not None
        except ImportError:
            pytest.skip("CTP extension not available")

    def test_ctp_structs_trade_import(self):
        """Test trade structs can be imported."""
        try:
            from bt_api_py.ctp import ctp_structs_trade

            assert ctp_structs_trade is not None
        except ImportError:
            pytest.skip("CTP extension not available")
