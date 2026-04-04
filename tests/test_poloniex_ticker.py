"""Tests for Poloniex ticker data container."""

import pytest

from bt_api_py.containers.tickers.poloniex_ticker import PoloniexRequestTickerData


class TestPoloniexRequestTickerData:
    """Tests for PoloniexRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "symbol": "BTC_USDT",
            "close": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
        }
        ticker = PoloniexRequestTickerData(
            ticker_info, "BTC_USDT", "SPOT", has_been_json_encoded=True
        )

        assert ticker.symbol_name == "BTC_USDT"
        assert ticker.exchange_name == "POLONIEX"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "symbol": "BTC_USDT",
            "close": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
        }
        ticker = PoloniexRequestTickerData(
            ticker_info, "BTC_USDT", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 50000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
