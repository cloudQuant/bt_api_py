"""Tests for MexcTradeData container."""

from bt_api_py.containers.trades.mexc_trade import MexcTradeData


class TestMexcTradeData:
    """Tests for MexcTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = MexcTradeData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert trade.exchange_name == "MEXC"
        assert trade.symbol_name == "BTCUSDT"
        assert trade.asset_type == "SPOT"
        assert trade.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with trade info."""
        data = {
            "id": "123456",
            "orderId": "789",
            "price": "50000.0",
            "qty": "1.0",
            "quoteQty": "50000.0",
            "commission": "0.01",
            "commissionAsset": "USDT",
            "time": 1234567890,
            "isBuyer": True,
            "isMaker": False,
        }
        trade = MexcTradeData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        trade.init_data()

        assert trade.trade_id == 123456
        assert trade.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        trade = MexcTradeData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = trade.get_all_data()

        assert result["exchange_name"] == "MEXC"
        assert result["symbol_name"] == "BTCUSDT"

    def test_str_representation(self):
        """Test __str__ method."""
        trade = MexcTradeData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(trade)

        assert "MEXC" in result
