"""Tests for Bitvavo ticker data container."""

import pytest

from bt_api_py.containers.tickers.bitvavo_ticker import BitvavoRequestTickerData


class TestBitvavoRequestTickerData:
    """Tests for BitvavoRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "market": "BTC-EUR",
            "last": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
            "volume": 1000.0,
            "high": 51000.0,
            "low": 49000.0,
        }
        ticker = BitvavoRequestTickerData(
            ticker_info, "BTC-EUR", "SPOT", has_been_json_encoded=True
        )

        assert ticker.symbol_name == "BTC-EUR"
        assert ticker.exchange_name == "BITVAVO"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "market": "BTC-EUR",
            "last": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
            "volume": 1000.0,
            "high": 51000.0,
            "low": 49000.0,
        }
        ticker = BitvavoRequestTickerData(
            ticker_info, "BTC-EUR", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49999.0
        assert ticker.ask_price == 50001.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
