"""Tests for HtxRequestOrderBookData container."""

from bt_api_py.containers.orderbooks.htx_orderbook import HtxRequestOrderBookData


class TestHtxRequestOrderBookData:
    """Tests for HtxRequestOrderBookData."""

    def test_init(self):
        """Test initialization."""
        orderbook = HtxRequestOrderBookData({}, symbol_name="btcusdt", asset_type="SPOT")

        assert orderbook.exchange_name == "HTX"
        assert orderbook.symbol_name == "btcusdt"
        assert orderbook.asset_type == "SPOT"
        assert orderbook.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with orderbook info."""
        data = {"tick": {"bids": [[50000.0, 1.0]], "asks": [[50010.0, 1.0]]}}
        orderbook = HtxRequestOrderBookData(
            data, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        orderbook.init_data()

        assert orderbook.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data method."""
        orderbook = HtxRequestOrderBookData(
            {}, symbol_name="btcusdt", asset_type="SPOT", has_been_json_encoded=True
        )
        result = orderbook.get_all_data()

        assert result["exchange_name"] == "HTX"
        assert result["symbol_name"] == "btcusdt"
