"""Tests for BybitOrderBookData container."""

import pytest

from bt_api_py.containers.orderbooks.bybit_orderbook import BybitOrderBookData


class TestBybitOrderBookData:
    """Tests for BybitOrderBookData."""

    def test_init(self):
        """Test initialization."""
        orderbook = BybitOrderBookData({}, symbol_name="BTCUSDT", asset_type="SPOT")

        assert orderbook.exchange_name == "BYBIT"
        assert orderbook.symbol_name == "BTCUSDT"
        assert orderbook.asset_type == "SPOT"
        assert orderbook.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with orderbook info."""
        data = {"result": {"b": [["50000.0", "1.0"]], "a": [["50010.0", "1.0"]]}}
        orderbook = BybitOrderBookData(data, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        orderbook.init_data()

        assert orderbook.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data method."""
        orderbook = BybitOrderBookData({}, symbol_name="BTCUSDT", asset_type="SPOT", has_been_json_encoded=True)
        result = orderbook.get_all_data()

        assert result["exchange_name"] == "BYBIT"
        assert result["symbol_name"] == "BTCUSDT"
