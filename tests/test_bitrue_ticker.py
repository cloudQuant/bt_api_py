"""Tests for Bitrue ticker data container."""

import pytest

from bt_api_py.containers.tickers.bitrue_ticker import BitrueRequestTickerData


class TestBitrueRequestTickerData:
    """Tests for BitrueRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "symbol": "BTCUSDT",
            "lastPrice": 50000.0,
            "bidPrice": 49999.0,
            "askPrice": 50001.0,
        }
        ticker = BitrueRequestTickerData(ticker_info, "BTCUSDT", "SPOT", has_been_json_encoded=True)

        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.exchange_name == "BITRUE"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "symbol": "BTCUSDT",
            "lastPrice": 50000.0,
            "bidPrice": 49999.0,
            "askPrice": 50001.0,
        }
        ticker = BitrueRequestTickerData(ticker_info, "BTCUSDT", "SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 50000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
