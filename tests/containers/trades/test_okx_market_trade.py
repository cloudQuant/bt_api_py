"""Tests for OkxMarketTradeData container."""

import pytest

from bt_api_py.containers.trades.okx_market_trade import OkxMarketTradeData


class TestOkxMarketTradeData:
    """Tests for OkxMarketTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = OkxMarketTradeData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert trade.exchange_name == "OKX"
        assert trade.symbol_name == "BTC-USDT"
        assert trade.asset_type == "SPOT"
        assert trade.has_been_init_data is False

    def test_init_data_dict_format(self):
        """Test init_data with dict format."""
        data = {
            "tradeId": "123456",
            "instId": "BTC-USDT",
            "px": "50000.0",
            "sz": "1.0",
            "side": "buy",
            "ts": "1234567890000",
        }
        trade = OkxMarketTradeData(data, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        trade.init_data()

        assert trade.trade_id == 123456
        assert trade.trade_symbol_name == "BTC-USDT"

    def test_get_all_data(self):
        """Test get_all_data."""
        trade = OkxMarketTradeData({}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        result = trade.get_all_data()

        assert result["exchange_name"] == "OKX"
        assert result["symbol_name"] == "BTC-USDT"

    def test_str_representation(self):
        """Test __str__ method."""
        trade = OkxMarketTradeData({}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        result = str(trade)

        assert "OKX" in result
