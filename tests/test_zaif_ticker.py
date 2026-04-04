"""Tests for Zaif ticker data container."""

import pytest

from bt_api_py.containers.tickers.zaif_ticker import ZaifRequestTickerData


class TestZaifRequestTickerData:
    """Tests for ZaifRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "last": 5000000.0,
            "bid": 4999999.0,
            "ask": 5000001.0,
            "volume": 100.0,
        }
        ticker = ZaifRequestTickerData(
            ticker_info, "BTC_JPY", "SPOT", has_been_json_encoded=True
        )

        assert ticker.symbol_name == "BTC_JPY"
        assert ticker.exchange_name == "ZAIF"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "last": 5000000.0,
            "bid": 4999999.0,
            "ask": 5000001.0,
            "volume": 100.0,
        }
        ticker = ZaifRequestTickerData(
            ticker_info, "BTC_JPY", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 5000000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
