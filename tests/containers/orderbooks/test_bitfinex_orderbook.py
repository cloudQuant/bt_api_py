"""Tests for BitfinexOrderBookData container."""


from bt_api_py.containers.orderbooks.bitfinex_orderbook import BitfinexOrderBookData


class TestBitfinexOrderBookData:
    """Tests for BitfinexOrderBookData."""

    def test_init(self):
        """Test initialization."""
        orderbook = BitfinexOrderBookData({}, symbol_name="BTCUSD", asset_type="SPOT")

        assert orderbook.exchange_name == "BITFINEX"
        assert orderbook.symbol_name == "BTCUSD"
        assert orderbook.asset_type == "SPOT"
        assert orderbook.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with orderbook info."""
        data = {"bids": [["50000.0", "1.0"]], "asks": [["50010.0", "1.0"]]}
        orderbook = BitfinexOrderBookData(
            data, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        orderbook.init_data()

        assert orderbook.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data method."""
        orderbook = BitfinexOrderBookData(
            {}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = orderbook.get_all_data()

        assert result["exchange_name"] == "BITFINEX"
        assert result["symbol_name"] == "BTCUSD"
