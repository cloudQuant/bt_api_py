"""Tests for CoinSpot ticker data container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.coinspot_ticker import CoinSpotRequestTickerData


class TestCoinSpotRequestTickerData:
    """Tests for CoinSpotRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "status": "ok",
            "prices": {
                "BTC": {
                    "last": 50000.0,
                    "bid": 49999.0,
                    "ask": 50001.0,
                }
            },
        }
        ticker = CoinSpotRequestTickerData(ticker_info, "BTC", "SPOT", has_been_json_encoded=True)

        assert ticker.ticker_symbol_name == "BTC"
        assert ticker.exchange_name == "COINSPOT"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "status": "ok",
            "prices": {
                "BTC": {
                    "last": 50000.0,
                    "bid": 49999.0,
                    "ask": 50001.0,
                }
            },
        }
        ticker = CoinSpotRequestTickerData(ticker_info, "BTC", "SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 50000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
