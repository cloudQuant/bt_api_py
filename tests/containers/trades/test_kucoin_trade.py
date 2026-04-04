"""Tests for KuCoinTradeData container."""

import pytest

from bt_api_py.containers.trades.kucoin_trade import KuCoinTradeData


class TestKuCoinTradeData:
    """Tests for KuCoinTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = KuCoinTradeData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert trade.exchange_name == "KUCOIN"
        assert trade.symbol_name == "BTC-USDT"
        assert trade.asset_type == "SPOT"
        assert trade.has_been_init_data is False

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        trade = KuCoinTradeData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        with pytest.raises(NotImplementedError):
            trade.init_data()

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        trade = KuCoinTradeData({}, symbol_name="BTC-USDT", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin
        trade._initialized = True
        assert trade.get_exchange_name() == "KUCOIN"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        trade = KuCoinTradeData({}, symbol_name="BTC-USDT", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin
        trade._initialized = True
        assert trade.get_symbol_name() == "BTC-USDT"

    def test_get_all_data(self):
        """Test get_all_data."""
        trade = KuCoinTradeData({}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        # Set _initialized to prevent AutoInitMixin
        trade._initialized = True
        result = trade.get_all_data()

        assert result["exchange_name"] == "KUCOIN"
        assert result["symbol_name"] == "BTC-USDT"
