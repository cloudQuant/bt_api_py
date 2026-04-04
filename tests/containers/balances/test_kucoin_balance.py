"""Tests for KuCoin balance containers."""

import pytest

from bt_api_py.containers.balances.kucoin_balance import (
    KuCoinBalanceData,
    KuCoinRequestBalanceData,
    KuCoinWssBalanceData,
)


class TestKuCoinBalanceData:
    """Tests for KuCoinBalanceData base class."""

    def test_init(self):
        """Test initialization."""
        balance = KuCoinBalanceData({}, symbol_name="BTC", asset_type="SPOT")

        assert balance.exchange_name == "KUCOIN"
        assert balance.symbol_name == "BTC"
        assert balance.asset_type == "SPOT"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        balance = KuCoinBalanceData({}, symbol_name="BTC", asset_type="SPOT")

        with pytest.raises(NotImplementedError):
            balance.init_data()

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = KuCoinBalanceData({}, symbol_name="BTC", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True
        assert balance.get_exchange_name() == "KUCOIN"

    def test_get_all_data(self):
        """Test get_all_data."""
        balance = KuCoinBalanceData({}, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        # Set _initialized to prevent AutoInitMixin issues
        balance._initialized = True
        result = balance.get_all_data()

        assert result["exchange_name"] == "KUCOIN"
        assert result["symbol_name"] == "BTC"


class TestKuCoinRequestBalanceData:
    """Tests for KuCoinRequestBalanceData."""

    def test_init(self):
        """Test initialization."""
        balance = KuCoinRequestBalanceData({}, symbol_name="BTC", asset_type="SPOT")

        assert balance.exchange_name == "KUCOIN"
        assert balance.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with balance info."""
        data = {
            "data": [
                {
                    "currency": "BTC",
                    "type": "trade",
                    "balance": "1.0",
                    "available": "0.8",
                    "holds": "0.2",
                }
            ]
        }
        balance = KuCoinRequestBalanceData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.symbol_name == "BTC"
        assert balance.margin == 1.0
        assert balance.available_margin == 0.8
        assert balance.used_margin == 0.2

    def test_init_data_list_format(self):
        """Test init_data with list format."""
        data = [
            {
                "currency": "USDT",
                "balance": "10000.0",
                "available": "9000.0",
                "holds": "1000.0",
            }
        ]
        balance = KuCoinRequestBalanceData(data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.symbol_name == "USDT"
        assert balance.margin == 10000.0

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        balance = KuCoinRequestBalanceData({}, symbol_name="BTC", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin from calling init_data
        balance._initialized = True
        assert balance.get_exchange_name() == "KUCOIN"


class TestKuCoinWssBalanceData:
    """Tests for KuCoinWssBalanceData."""

    def test_init_data(self):
        """Test init_data with WebSocket format."""
        data = {
            "data": {
                "currency": "BTC",
                "balance": "1.0",
                "available": "0.8",
                "holds": "0.2",
            }
        }
        balance = KuCoinWssBalanceData(data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.symbol_name == "BTC"
        assert balance.margin == 1.0
        assert balance.available_margin == 0.8

    def test_init_data_direct_format(self):
        """Test init_data with direct data format."""
        data = {
            "currency": "USDT",
            "balance": "10000.0",
            "available": "9000.0",
            "holds": "1000.0",
        }
        balance = KuCoinWssBalanceData(data, symbol_name="USDT", asset_type="SPOT", has_been_json_encoded=True)
        balance.init_data()

        assert balance.symbol_name == "USDT"
        assert balance.margin == 10000.0
