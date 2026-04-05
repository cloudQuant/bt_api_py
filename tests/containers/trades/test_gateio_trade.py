"""Tests for GateioTradeData container."""


from bt_api_py.containers.trades.gateio_trade import GateioTradeData


class TestGateioTradeData:
    """Tests for GateioTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = GateioTradeData({}, symbol_name="BTC_USDT", asset_type="SPOT")

        assert trade.exchange_name == "GATEIO"
        assert trade.symbol_name == "BTC_USDT"
        assert trade.asset_type == "SPOT"
        assert trade.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with trade info."""
        data = {
            "id": "123456",
            "order_id": "abc123",
            "side": "buy",
            "price": "50000.0",
            "amount": "1.0",
            "fee": "0.01",
        }
        trade = GateioTradeData(
            data, symbol_name="BTC_USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        trade.init_data()

        assert trade.trade_id == "123456"
        assert trade.side == "buy"
        assert trade.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        trade = GateioTradeData(
            {}, symbol_name="BTC_USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = trade.get_all_data()

        assert result["exchange_name"] == "GATEIO"
        assert result["symbol_name"] == "BTC_USDT"

    def test_str_representation(self):
        """Test __str__ method."""
        trade = GateioTradeData(
            {}, symbol_name="BTC_USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(trade)

        assert "GATEIO" in result
