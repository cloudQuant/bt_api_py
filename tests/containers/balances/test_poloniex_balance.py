"""Tests for Poloniex balance containers."""

from __future__ import annotations

import pytest

from bt_api_py.containers.balances.poloniex_balance import (
    PoloniexBalanceData,
    PoloniexRequestBalanceData,
)


class TestPoloniexBalanceData:
    """Tests for PoloniexBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = PoloniexBalanceData({}, symbol_name="BTC", asset_type="SPOT")

        assert balance.exchange_name == "POLONIEX"
        assert balance.symbol_name == "BTC"
        assert balance.asset_type == "SPOT"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        balance = PoloniexBalanceData({}, symbol_name="BTC", asset_type="SPOT")

        with pytest.raises(NotImplementedError):
            balance.init_data()

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = PoloniexBalanceData({}, symbol_name="BTC", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin
        balance._initialized = True
        assert balance.get_exchange_name() == "POLONIEX"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        balance = PoloniexBalanceData({}, symbol_name="BTC", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin
        balance._initialized = True
        assert balance.get_symbol_name() == "BTC"

    def test_get_all_data(self):
        """Test get_all_data."""
        balance = PoloniexBalanceData(
            {}, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        # Set _initialized to prevent AutoInitMixin
        balance._initialized = True
        result = balance.get_all_data()

        assert result["exchange_name"] == "POLONIEX"
        assert result["symbol_name"] == "BTC"


class TestPoloniexRequestBalanceData:
    """Tests for PoloniexRequestBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = PoloniexRequestBalanceData({}, symbol_name="BTC", asset_type="SPOT")

        assert balance.exchange_name == "POLONIEX"
        assert balance.symbol_name == "BTC"
