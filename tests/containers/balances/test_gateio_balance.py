"""Tests for GateioBalanceData container."""

import pytest

from bt_api_py.containers.balances.gateio_balance import (
    GateioBalanceData,
    GateioRequestBalanceData,
)


class TestGateioBalanceData:
    """Tests for GateioBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = GateioBalanceData({}, asset_type="SPOT")

        assert balance.exchange_name == "GATEIO"
        assert balance.asset_type == "SPOT"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "currency": "USDT",
            "available": "1000.0",
            "locked": "100.0",
        }
        balance = GateioBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.currency == "USDT"
        assert balance.available == 1000.0
        assert balance.locked == 100.0

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = GateioBalanceData({}, asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin
        balance._initialized = True
        assert balance.get_exchange_name() == "GATEIO"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        balance = GateioBalanceData({}, asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin
        balance._initialized = True
        assert balance.get_asset_type() == "SPOT"

    def test_get_total(self):
        """Test get_total."""
        data = {"currency": "USDT", "available": "1000.0", "locked": "100.0"}
        balance = GateioBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)

        assert balance.get_total() == 1100.0

    def test_is_zero_balance(self):
        """Test is_zero_balance."""
        data = {"currency": "USDT", "available": "0.0", "locked": "0.0"}
        balance = GateioBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.is_zero_balance() is True

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"currency": "USDT", "available": "1000.0", "locked": "100.0"}
        balance = GateioBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        result = balance.get_all_data()

        assert result["exchange_name"] == "GATEIO"
        assert result["currency"] == "USDT"


class TestGateioRequestBalanceData:
    """Tests for GateioRequestBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = GateioRequestBalanceData({}, asset_type="SPOT")

        assert balance.exchange_name == "GATEIO"
        assert balance.asset_type == "SPOT"
