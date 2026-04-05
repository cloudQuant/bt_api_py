"""Tests for BigONE ticker data container."""

import pytest

from bt_api_py.containers.tickers.bigone_ticker import BigONERequestTickerData


class TestBigONERequestTickerData:
    """Tests for BigONERequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "data": {
                "asset_pair_name": "BTCUSDT",
                "close": 50000.0,
                "bid": {"price": 49999.0},
                "ask": {"price": 50001.0},
                "volume": 1000.0,
            }
        }
        ticker = BigONERequestTickerData(ticker_info, "BTCUSDT", "SPOT", has_been_json_encoded=True)

        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.exchange_name == "BIGONE"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "data": {
                "asset_pair_name": "BTCUSDT",
                "close": 50000.0,
                "bid": {"price": 49999.0},
                "ask": {"price": 50001.0},
                "volume": 1000.0,
            }
        }
        ticker = BigONERequestTickerData(ticker_info, "BTCUSDT", "SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 50000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
