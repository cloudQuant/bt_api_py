"""Tests for CoinDCX ticker data container."""

import pytest

from bt_api_py.containers.tickers.coindcx_ticker import CoinDCXRequestTickerData


class TestCoinDCXRequestTickerData:
    """Tests for CoinDCXRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "data": {
                "market": "BTCINR",
                "last_price": 5000000.0,
                "bid": 4999999.0,
                "ask": 5000001.0,
                "volume": 10.5,
            }
        }
        ticker = CoinDCXRequestTickerData(
            ticker_info, "BTCINR", "SPOT", has_been_json_encoded=True
        )

        assert ticker.symbol_name == "BTCINR"
        assert ticker.exchange_name == "COINDCX"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "data": {
                "market": "BTCINR",
                "last_price": 5000000.0,
                "bid": 4999999.0,
                "ask": 5000001.0,
                "volume": 10.5,
            }
        }
        ticker = CoinDCXRequestTickerData(
            ticker_info, "BTCINR", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 5000000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
