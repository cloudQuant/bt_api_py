"""Tests for MexcBalanceData container."""

import pytest

from bt_api_py.containers.balances.mexc_balance import (
    MexcAccountData,
    MexcBalanceData,
    MexcRequestBalanceData,
)


class TestMexcBalanceData:
    """Tests for MexcBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = MexcBalanceData({}, symbol_name="BTC", asset_type="SPOT")

        assert balance.exchange_name == "MEXC"
        assert balance.symbol_name == "BTC"
        assert balance.asset_type == "SPOT"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "asset": "USDT",
            "free": "1000.0",
            "locked": "100.0",
        }
        balance = MexcBalanceData(data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.asset == "USDT"
        assert balance.free == 1000.0
        assert balance.locked == 100.0

    def test_get_all_data(self):
        """Test get_all_data."""
        data = {"asset": "USDT", "free": "1000.0", "locked": "100.0"}
        balance = MexcBalanceData(data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True)
        result = balance.get_all_data()

        assert result["exchange_name"] == "MEXC"
        assert result["asset"] == "USDT"

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"asset": "USDT", "free": "1000.0"}
        balance = MexcBalanceData(data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True)
        result = str(balance)

        assert "MEXC" in result

    def test_init_data_from_json_string(self):
        payload = '{"asset": "BTC", "free": "1.5", "locked": "0.5"}'
        balance = MexcBalanceData(payload, symbol_name="BTC", asset_type="SPOT")
        balance.init_data()

        assert balance.get_asset() == "BTC"
        assert balance.get_total() == 2.0

    def test_balance_helper_methods(self):
        balance = MexcBalanceData(
            {"asset": "ETH", "free": "2.0", "locked": "1.0"},
            symbol_name="ETH",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )

        assert balance.get_available() == 2.0
        assert balance.get_frozen() == 1.0
        assert balance.has_available() is True
        assert balance.has_frozen() is True
        assert balance.is_zero() is False


class TestMexcRequestBalanceData:
    def test_request_balance_init_data(self):
        balance = MexcRequestBalanceData(
            {"asset": "USDT", "free": "5.0", "locked": "0.5"},
            symbol_name="USDT",
            asset_type="SPOT",
            has_been_json_encoded=True,
        )
        balance.init_data()

        assert balance.get_asset() == "USDT"
        assert balance.get_total() == 5.5


class TestMexcAccountData:
    def test_account_data_parsing_and_balance_lookup(self):
        account = MexcAccountData(
            {
                "makerCommission": "10",
                "takerCommission": "20",
                "buyerCommission": "1",
                "sellerCommission": "2",
                "canTrade": True,
                "canWithdraw": True,
                "canDeposit": False,
                "accountType": "SPOT",
                "balances": [
                    {"asset": "BTC", "free": "1.0", "locked": "0.2"},
                    {"asset": "USDT", "free": "1000", "locked": "0"},
                ],
            },
            asset_type="SPOT",
            has_been_json_encoded=True,
        )

        data = account.get_all_data()
        assert data["maker_commission"] == 10
        assert account.get_taker_commission() == 20
        assert account.get_buyer_commission() == 1
        assert account.get_seller_commission() == 2
        assert account.get_can_trade() is True
        assert account.get_can_withdraw() is True
        assert account.get_can_deposit() is False
        assert account.get_account_type() == "SPOT"
        assert len(account.get_balances()) == 2
        assert account.get_balance_by_asset("BTC") is not None
        assert account.get_total_balance_by_asset("BTC") == 1.2
        assert account.get_available_balance_by_asset("BTC") == 1.0
        assert account.get_frozen_balance_by_asset("BTC") == 0.2
        assert account.get_balance_by_asset("ETH") is None
