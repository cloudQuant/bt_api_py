"""Tests for BitgetTradeData container."""

from __future__ import annotations

from bt_api_py.containers.trades.bitget_trade import BitgetTradeData


class TestBitgetTradeData:
    """Tests for BitgetTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = BitgetTradeData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert trade.exchange_name == "BITGET"
        assert trade.symbol_name == "BTCUSDT"
        assert trade.asset_type == "SPOT"
        assert trade.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with trade info."""
        data = {
            "tradeId": "123456",
            "orderId": "abc123",
            "symbol": "BTCUSDT",
            "side": "buy",
            "price": "50000.0",
            "size": "1.0",
            "fee": "0.01",
        }
        trade = BitgetTradeData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        trade.init_data()

        assert trade.trade_id == "123456"
        assert trade.side == "buy"
        assert trade.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        trade = BitgetTradeData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = trade.get_all_data()

        assert result["exchange_name"] == "BITGET"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        trade = BitgetTradeData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(trade)

        assert "BITGET" in result
