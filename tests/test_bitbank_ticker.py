"""Tests for BitBank ticker data container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.bitbank_ticker import BitbankRequestTickerData


class TestBitbankRequestTickerData:
    """Tests for BitbankRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "data": {
                "pair": "btc_jpy",
                "last": 5000000.0,
                "buy": 4999999.0,
                "sell": 5000001.0,
                "vol": 100.0,
            }
        }
        ticker = BitbankRequestTickerData(
            ticker_info, "BTC_JPY", "SPOT", has_been_json_encoded=True
        )

        assert ticker.symbol_name == "BTC_JPY"
        assert ticker.exchange_name == "BITBANK"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "data": {
                "pair": "btc_jpy",
                "last": 5000000.0,
                "buy": 4999999.0,
                "sell": 5000001.0,
                "vol": 100.0,
            }
        }
        ticker = BitbankRequestTickerData(
            ticker_info, "BTC_JPY", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 5000000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
