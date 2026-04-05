"""Tests for HitBtcRequestTradeData container."""


from bt_api_py.containers.trades.hitbtc_trade import HitBtcRequestTradeData


class TestHitBtcRequestTradeData:
    """Tests for HitBtcRequestTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = HitBtcRequestTradeData({}, symbol_name="BTCUSD", asset_type="SPOT")

        assert trade.exchange_name == "HITBTC"
        assert trade.symbol_name == "BTCUSD"
        assert trade.asset_type == "SPOT"
        assert trade.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with trade info."""
        data = {
            "id": "123456",
            "price": "50000.0",
            "quantity": "1.0",
            "side": "buy",
            "timestamp": 1234567890.0,
        }
        trade = HitBtcRequestTradeData(
            data, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        trade.init_data()

        assert trade.trade_id == "123456"
        assert trade.side == "buy"
        assert trade.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        trade = HitBtcRequestTradeData(
            {}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = trade.get_all_data()

        assert result["exchange_name"] == "HITBTC"
        assert result["symbol_name"] == "BTCUSD"

    def test_str_representation(self):
        """Test __str__ method."""
        trade = HitBtcRequestTradeData(
            {}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(trade)

        assert "HITBTC" in result
