"""Tests for HtxRequestTradeData container."""


from bt_api_py.containers.trades.htx_trade import HtxRequestTradeData


class TestHtxRequestTradeData:
    """Tests for HtxRequestTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = HtxRequestTradeData({}, symbol_name="btcusdt", asset_type="SPOT")

        assert trade.exchange_name == "HTX"
        assert trade.symbol_name == "btcusdt"
        assert trade.asset_type == "SPOT"
        assert trade.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with trade info."""
        data = {
            "id": 123456,
            "order-id": 789,
            "symbol": "btcusdt",
            "type": "buy-limit",
            "price": "50000",
            "filled-amount": "1.0",
            "filled-fees": "0.01",
        }
        trade = HtxRequestTradeData(
            data, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        trade.init_data()

        assert trade.trade_id is not None
        assert trade.trade_side is not None

    def test_get_all_data(self):
        """Test get_all_data."""
        trade = HtxRequestTradeData(
            {}, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        result = trade.get_all_data()

        assert result["exchange_name"] == "HTX"
        assert result["symbol_name"] == "btcusdt"

    def test_str_representation(self):
        """Test __str__ method."""
        trade = HtxRequestTradeData(
            {}, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(trade)

        assert "HTX" in result
