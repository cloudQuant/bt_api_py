"""Tests for BitgetOrderBookData container."""

from __future__ import annotations

from bt_api_py.containers.orderbooks.bitget_orderbook import BitgetOrderBookData


class TestBitgetOrderBookData:
    """Tests for BitgetOrderBookData."""

    def test_init(self):
        """Test initialization."""
        orderbook = BitgetOrderBookData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert orderbook.exchange_name == "BITGET"
        assert orderbook.symbol_name == "BTCUSDT"
        assert orderbook.asset_type == "SPOT"
        assert orderbook.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with orderbook info."""
        data = {"data": {"bids": [["50000.0", "1.0"]], "asks": [["50010.0", "1.0"]]}}
        orderbook = BitgetOrderBookData(
            data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        orderbook.init_data()

        assert orderbook.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data method."""
        orderbook = BitgetOrderBookData(
            {}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True
        )
        result = orderbook.get_all_data()

        assert result["exchange_name"] == "BITGET"
        assert result["symbol_name"] == "BTCUSDT"
