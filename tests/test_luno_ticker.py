"""Tests for Luno ticker data container."""

import pytest

from bt_api_py.containers.tickers.luno_ticker import LunoRequestTickerData


class TestLunoRequestTickerData:
    """Tests for LunoRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "pair": "XBTZAR",
            "last_trade": 500000.0,
            "bid": 499999.0,
            "ask": 500001.0,
            "rolling_24_hour_volume": 10.5,
            "rolling_24_hour_high": 510000.0,
            "rolling_24_hour_low": 490000.0,
            "timestamp": 1705315800,
        }
        ticker = LunoRequestTickerData(
            ticker_info, "XBTZAR", "SPOT", has_been_json_encoded=True
        )

        assert ticker.symbol_name == "XBTZAR"
        assert ticker.asset_type == "SPOT"
        assert ticker.exchange_name == "LUNO"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "pair": "XBTZAR",
            "last_trade": 500000.0,
            "bid": 499999.0,
            "ask": 500001.0,
            "rolling_24_hour_volume": 10.5,
            "rolling_24_hour_high": 510000.0,
            "rolling_24_hour_low": 490000.0,
            "timestamp": 1705315800,
        }
        ticker = LunoRequestTickerData(
            ticker_info, "XBTZAR", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 500000.0
        assert ticker.bid_price == 499999.0
        assert ticker.ask_price == 500001.0
        assert ticker.volume_24h == 10.5
        assert ticker.high_24h == 510000.0
        assert ticker.low_24h == 490000.0
        assert ticker.timestamp == 1705315800
        assert ticker.ticker_symbol_name == "XBTZAR"

    def test_init_data_missing_fields(self):
        """Test init_data with missing fields."""
        ticker_info = {"pair": "XBTZAR"}
        ticker = LunoRequestTickerData(
            ticker_info, "XBTZAR", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price is None
        assert ticker.bid_price is None
        assert ticker.ask_price is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
