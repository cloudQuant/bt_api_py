"""Tests for BitfinexRequestTradeData container."""

from bt_api_py.containers.trades.bitfinex_trade import BitfinexRequestTradeData


class TestBitfinexRequestTradeData:
    """Tests for BitfinexRequestTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = BitfinexRequestTradeData({}, symbol_name="BTCUSD", asset_type="SPOT")

        assert trade.exchange_name == "BITFINEX"
        assert trade.symbol_name == "BTCUSD"
        assert trade.asset_type == "SPOT"
        assert trade.has_been_init_data is False

    def test_init_data_dict_format(self):
        """Test init_data with dict format."""
        data = [
            {
                "id": "123456",
                "price": "50000.0",
                "amount": "1.0",
                "timestamp": 1234567890,
                "side": "BUY",
            }
        ]
        trade = BitfinexRequestTradeData(
            data, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        trade.init_data()

        assert trade.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        trade = BitfinexRequestTradeData(
            {}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = trade.get_all_data()

        assert result["exchange_name"] == "BITFINEX"
        assert result["symbol_name"] == "BTCUSD"

    def test_str_representation(self):
        """Test __str__ method."""
        trade = BitfinexRequestTradeData(
            {}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(trade)

        assert "BITFINEX" in result
