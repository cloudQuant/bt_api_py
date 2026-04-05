"""Tests for GateioBalanceData container."""

from __future__ import annotations

import json

from bt_api_py.containers.balances.gateio_balance import (
    GateioAccountBalance,
    GateioBalanceData,
    GateioRequestBalanceData,
    GateioWssBalanceData,
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

    def test_init_data_from_json_string(self):
        payload = json.dumps({"currency": "BTC", "available": "1.5", "locked": "0.2"})
        balance = GateioBalanceData(payload, asset_type="SPOT")
        balance.init_data()

        assert balance.get_currency() == "BTC"
        assert balance.get_total() == 1.7

    def test_non_zero_balance_detection(self):
        data = {"currency": "USDT", "available": "2.0", "locked": "0.0"}
        balance = GateioBalanceData(data, asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.is_zero_balance() is False


class TestGateioRequestBalanceData:
    """Tests for GateioRequestBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = GateioRequestBalanceData({}, asset_type="SPOT")

        assert balance.exchange_name == "GATEIO"
        assert balance.asset_type == "SPOT"

    def test_request_balance_init_data(self):
        balance = GateioRequestBalanceData(
            {"currency": "ETH", "available": "5.0", "locked": "1.0"},
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        balance.init_data()

        assert balance.get_currency() == "ETH"
        assert balance.get_total() == 6.0


class TestGateioWssBalanceData:
    def test_wss_init_returns_self_without_parsing(self):
        balance = GateioWssBalanceData(
            {"currency": "BTC"},
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        assert balance.init_data() is balance
        assert balance.get_currency() is None


class TestGateioAccountBalance:
    def test_account_balance_helpers(self):
        account = GateioAccountBalance(
            [
                {"currency": "USDT", "available": "100", "locked": "0"},
                {"currency": "BTC", "available": "1", "locked": "0.5"},
            ],
            asset_type="SPOT",
        )

        all_balances = account.get_all_balances()
        assert len(all_balances) == 2
        assert account.get_balance("BTC")["currency"] == "BTC"
        assert len(account.get_nonzero_balances()) == 2
        assert account.get_total_value({"BTC": 50000.0, "USDT": 1.0}) == 75100.0

    def test_account_balance_missing_currency_returns_none(self):
        account = GateioAccountBalance([], asset_type="SPOT")
        assert account.get_balance("ETH") is None
