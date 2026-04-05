"""Tests for Bitso ticker data container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.bitso_ticker import BitsoRequestTickerData


class TestBitsoRequestTickerData:
    """Tests for BitsoRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "payload": {
                "book": "BTC_MXN",
                "last": 5000000.0,
                "bid": 4999999.0,
                "ask": 5000001.0,
                "volume": 100.0,
            }
        }
        ticker = BitsoRequestTickerData(ticker_info, "BTC_MXN", "SPOT", has_been_json_encoded=True)

        assert ticker.symbol_name == "BTC_MXN"
        assert ticker.exchange_name == "BITSO"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "payload": {
                "book": "BTC_MXN",
                "last": 5000000.0,
                "bid": 4999999.0,
                "ask": 5000001.0,
                "volume": 100.0,
            }
        }
        ticker = BitsoRequestTickerData(ticker_info, "BTC_MXN", "SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 5000000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
