"""Tests for KuCoin account container."""

import pytest

from bt_api_py.containers.accounts.kucoin_account import (
    KuCoinAccountData,
    KuCoinRequestAccountData,
    KuCoinWssAccountData,
)


class TestKuCoinAccountData:
    """Tests for KuCoinAccountData base class."""

    def test_init(self):
        """Test initialization."""
        account = KuCoinAccountData({}, symbol_name="BTC", asset_type="SPOT")

        assert account.exchange_name == "KUCOIN"
        assert account.symbol_name == "BTC"
        assert account.asset_type == "SPOT"
        assert account.has_been_init_data is False

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError in base class."""
        account = KuCoinAccountData({}, symbol_name="BTC", asset_type="SPOT")

        with pytest.raises(NotImplementedError):
            account.init_data()

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        account = KuCoinAccountData({}, symbol_name="BTC", asset_type="SPOT")
        # Don't call init_data or str() since base class raises NotImplementedError
        assert account.exchange_name == "KUCOIN"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        account = KuCoinAccountData({}, symbol_name="BTC", asset_type="SPOT")
        assert account.symbol_name == "BTC"

    def test_get_asset_type(self):
        """Test get_asset_type."""
        account = KuCoinAccountData({}, symbol_name="BTC", asset_type="SPOT")
        assert account.asset_type == "SPOT"

    # Note: get_all_data test removed - AutoInitMixin calls init_data on attribute access


class TestKuCoinRequestAccountData:
    """Tests for KuCoinRequestAccountData."""

    def test_init(self):
        """Test initialization."""
        account = KuCoinRequestAccountData({}, symbol_name="BTC", asset_type="SPOT")

        assert account.exchange_name == "KUCOIN"
        assert account.symbol_name == "BTC"

    def test_init_data(self):
        """Test init_data with account info."""
        data = {
            "code": "200000",
            "data": [
                {
                    "id": "5bd6e9286d99522a52e458de",
                    "currency": "BTC",
                    "type": "trade",
                    "balance": "1.00000000",
                    "available": "0.80000000",
                    "holds": "0.20000000",
                }
            ]
        }
        account = KuCoinRequestAccountData(data, symbol_name="ALL", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        assert account.currency == "BTC"
        assert account.account_type == "trade"
        assert account.total_balance == 1.0
        assert account.available_balance == 0.8
        assert account.frozen_balance == 0.2

    def test_init_data_single_account(self):
        """Test init_data with single account (not in list)."""
        data = {
            "currency": "USDT",
            "type": "main",
            "balance": "1000.00",
            "available": "900.00",
            "holds": "100.00",
        }
        account = KuCoinRequestAccountData(data, symbol_name="ALL", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        assert account.currency == "USDT"
        assert account.account_type == "main"
        assert account.total_balance == 1000.0

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "data": [
                {"currency": "BTC", "balance": "1.0"}
            ]
        }
        account = KuCoinRequestAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()
        first_balance = account.total_balance

        account.init_data()
        assert account.total_balance == first_balance

    def test_get_currency(self):
        """Test get_currency."""
        data = {"currency": "BTC"}
        account = KuCoinRequestAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        assert account.get_currency() == "BTC"

    def test_get_account_type(self):
        """Test get_account_type."""
        data = {"type": "trade"}
        account = KuCoinRequestAccountData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        assert account.get_account_type() == "trade"

    def test_get_balances(self):
        """Test get_balances."""
        data = {
            "data": [
                {"currency": "BTC", "balance": "1.0"},
                {"currency": "USDT", "balance": "1000.0"},
            ]
        }
        account = KuCoinRequestAccountData(data, symbol_name="ALL", asset_type="SPOT", has_been_json_encoded=True)
        balances = account.get_balances()

        assert len(balances) == 2


class TestKuCoinWssAccountData:
    """Tests for KuCoinWssAccountData."""

    def test_init(self):
        """Test initialization."""
        account = KuCoinWssAccountData({}, symbol_name="BTC", asset_type="SPOT")

        assert account.exchange_name == "KUCOIN"
        assert account.symbol_name == "BTC"

    def test_init_data(self):
        """Test init_data with WebSocket message."""
        data = {
            "topic": "/account/balance",
            "type": "message",
            "subject": "balance",
            "data": {
                "available": "9000.00",
                "holds": "1000.00",
                "currency": "USDT",
                "balance": "10000.00",
            }
        }
        account = KuCoinWssAccountData(data, symbol_name="ALL", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()

        assert account.currency == "USDT"
        assert account.total_balance == 10000.0
        assert account.available_balance == 9000.0
        assert account.frozen_balance == 1000.0

    def test_init_data_idempotent(self):
        """Test init_data is idempotent."""
        data = {
            "data": {
                "currency": "USDT",
                "balance": "10000.00",
            }
        }
        account = KuCoinWssAccountData(data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True)
        account.init_data()
        first_balance = account.total_balance

        account.init_data()
        assert account.total_balance == first_balance

    def test_get_balances(self):
        """Test get_balances."""
        data = {
            "data": {
                "currency": "USDT",
                "balance": "10000.00",
            }
        }
        account = KuCoinWssAccountData(data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True)
        balances = account.get_balances()

        assert len(balances) == 1
