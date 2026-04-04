"""Tests for OkxL2OrderBookData container."""

import pytest

from bt_api_py.containers.orderbooks.okx_l2_orderbook import OkxL2OrderBookData


class TestOkxL2OrderBookData:
    """Tests for OkxL2OrderBookData."""

    def test_init(self):
        """Test initialization."""
        orderbook = OkxL2OrderBookData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert orderbook.exchange_name == "OKX"
        assert orderbook.symbol_name == "BTC-USDT"
        assert orderbook.asset_type == "SPOT"
        assert orderbook.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with orderbook info."""
        data = {"data": [{"bids": [["50000.0", "1.0", "1", "0"]], "asks": [["50010.0", "1.0", "1", "0"]]}]}
        orderbook = OkxL2OrderBookData(data, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        orderbook.init_data()

        assert orderbook.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data method."""
        orderbook = OkxL2OrderBookData({}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        result = orderbook.get_all_data()

        assert result["exchange_name"] == "OKX"
        assert result["symbol_name"] == "BTC-USDT"
