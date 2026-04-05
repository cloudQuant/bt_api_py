"""Tests for YoBit ticker data container."""

import pytest

from bt_api_py.containers.tickers.yobit_ticker import YobitRequestTickerData


class TestYobitRequestTickerData:
    """Tests for YobitRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "btc_usd": {
                "last": 50000.0,
                "buy": 49999.0,
                "sell": 50001.0,
                "vol": 100.0,
            }
        }
        ticker = YobitRequestTickerData(ticker_info, "BTC_USD", "SPOT", has_been_json_encoded=True)

        assert ticker.symbol_name == "BTC_USD"
        assert ticker.exchange_name == "YOBIT"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "btc_usd": {
                "last": 50000.0,
                "buy": 49999.0,
                "sell": 50001.0,
                "vol": 100.0,
            }
        }
        ticker = YobitRequestTickerData(ticker_info, "BTC_USD", "SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 50000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
