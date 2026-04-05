"""Tests for Bitstamp ticker data container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.bitstamp_ticker import BitstampRequestTickerData


class TestBitstampRequestTickerData:
    """Tests for BitstampRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "symbol": "BTCUSD",
            "last": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
            "volume": 1000.0,
        }
        ticker = BitstampRequestTickerData(
            ticker_info, "BTCUSD", "SPOT", has_been_json_encoded=True
        )

        assert ticker.symbol_name == "BTCUSD"
        assert ticker.exchange_name == "BITSTAMP"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "symbol": "BTCUSD",
            "last": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
            "volume": 1000.0,
        }
        ticker = BitstampRequestTickerData(
            ticker_info, "BTCUSD", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 50000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
