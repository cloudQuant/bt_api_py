"""Tests for OkxOrderBookData container."""


from bt_api_py.containers.orderbooks.okx_orderbook import OkxOrderBookData


class TestOkxOrderBookData:
    """Tests for OkxOrderBookData."""

    def test_init(self):
        """Test initialization."""
        orderbook = OkxOrderBookData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert orderbook.exchange_name == "OKX"
        assert orderbook.symbol_name == "BTC-USDT"
        assert orderbook.asset_type == "SPOT"
        assert orderbook.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with orderbook info."""
        data = {"data": [{"bids": [["50000.0", "1.0"]], "asks": [["50010.0", "1.0"]]}]}
        orderbook = OkxOrderBookData(
            data, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        orderbook.init_data()

        assert orderbook.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data method."""
        orderbook = OkxOrderBookData(
            {}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = orderbook.get_all_data()

        assert result["exchange_name"] == "OKX"
        assert result["symbol_name"] == "BTC-USDT"
