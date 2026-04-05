"""Tests for CoinOne ticker data container."""

import pytest

from bt_api_py.containers.tickers.coinone_ticker import CoinoneRequestTickerData


class TestCoinoneRequestTickerData:
    """Tests for CoinoneRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "currency": "BTC",
            "last": 50000000.0,
            "bid": 49999999.0,
            "ask": 50000001.0,
            "volume": 100.0,
        }
        ticker = CoinoneRequestTickerData(ticker_info, "BTC", "SPOT", has_been_json_encoded=True)

        assert ticker.symbol_name == "BTC"
        assert ticker.exchange_name == "COINONE"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "currency": "BTC",
            "last": 50000000.0,
            "bid": 49999999.0,
            "ask": 50000001.0,
            "volume": 100.0,
        }
        ticker = CoinoneRequestTickerData(ticker_info, "BTC", "SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 50000000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
