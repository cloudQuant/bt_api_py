"""Tests for CoinEx ticker data container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.coinex_ticker import CoinExRequestTickerData


class TestCoinExRequestTickerData:
    """Tests for CoinExRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "data": {
                "market": "BTCUSDT",
                "last": 50000.0,
                "bid": 49999.0,
                "ask": 50001.0,
                "volume_24h": 1000.0,
            }
        }
        ticker = CoinExRequestTickerData(ticker_info, "BTCUSDT", "SPOT", has_been_json_encoded=True)

        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.exchange_name == "COINEX"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "data": {
                "market": "BTCUSDT",
                "last": 50000.0,
                "bid": 49999.0,
                "ask": 50001.0,
                "volume_24h": 1000.0,
            }
        }
        ticker = CoinExRequestTickerData(ticker_info, "BTCUSDT", "SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 50000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
