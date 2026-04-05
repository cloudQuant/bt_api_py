"""Tests for CoinbaseOrderBookData container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.orderbooks.coinbase_orderbook import CoinbaseOrderBookData


class TestCoinbaseOrderBookData:
    """Tests for CoinbaseOrderBookData."""

    def test_init(self):
        """Test initialization."""
        orderbook = CoinbaseOrderBookData({}, symbol_name="BTC-USD", asset_type="SPOT")

        assert orderbook.exchange_name == "COINBASE"
        assert orderbook.symbol_name == "BTC-USD"
        assert orderbook.asset_type == "SPOT"
        assert orderbook.has_been_init_data is False

    def test_init_data(self):
        """Test init_data - base class raises NotImplementedError."""
        data = {"bids": [["50000.0", "1.0"]], "asks": [["50010.0", "1.0"]]}
        orderbook = CoinbaseOrderBookData(
            data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )

        with pytest.raises(NotImplementedError):
            orderbook.init_data()

    def test_get_all_data(self):
        """Test get_all_data method - raises NotImplementedError via init_data."""
        orderbook = CoinbaseOrderBookData(
            {}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )

        with pytest.raises(NotImplementedError):
            orderbook.get_all_data()
