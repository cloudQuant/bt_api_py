"""Tests for CryptoComOrderBook container."""

import pytest

from bt_api_py.containers.orderbooks.cryptocom_orderbook import CryptoComOrderBook


class TestCryptoComOrderBook:
    """Tests for CryptoComOrderBook."""

    def test_init(self):
        """Test initialization."""
        orderbook = CryptoComOrderBook({}, symbol_name="BTC_USDT", asset_type="SPOT")

        assert orderbook.exchange_name == "CRYPTOCOM"
        assert orderbook.symbol_name == "BTC_USDT"
        assert orderbook.asset_type == "SPOT"
        assert orderbook.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with orderbook info."""
        data = {"result": {"bids": [["50000.0", "1.0"]], "asks": [["50010.0", "1.0"]]}}
        orderbook = CryptoComOrderBook(data, symbol_name="BTC_USDT", asset_type="SPOT", has_been_json_encoded=True)
        orderbook.init_data()

        assert orderbook.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data method."""
        orderbook = CryptoComOrderBook({}, symbol_name="BTC_USDT", asset_type="SPOT", has_been_json_encoded=True)
        result = orderbook.get_all_data()

        assert result["exchange_name"] == "CRYPTOCOM"
        assert result["symbol_name"] == "BTC_USDT"
