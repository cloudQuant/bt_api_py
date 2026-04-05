"""Tests for WazirX ticker data container."""

import pytest

from bt_api_py.containers.tickers.wazirx_ticker import WazirxRequestTickerData


class TestWazirxRequestTickerData:
    """Tests for WazirxRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "symbol": "btcinr",
            "lastPrice": 5000000.0,
            "bidPrice": 4999999.0,
            "askPrice": 5000001.0,
            "volume": 10.5,
            "high": 5100000.0,
            "low": 4900000.0,
        }
        ticker = WazirxRequestTickerData(ticker_info, "BTCINR", "SPOT", has_been_json_encoded=True)

        assert ticker.symbol_name == "BTCINR"
        assert ticker.exchange_name == "WAZIRX"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "symbol": "btcinr",
            "lastPrice": 5000000.0,
            "bidPrice": 4999999.0,
            "askPrice": 5000001.0,
            "volume": 10.5,
            "high": 5100000.0,
            "low": 4900000.0,
        }
        ticker = WazirxRequestTickerData(ticker_info, "BTCINR", "SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 5000000.0
        assert ticker.bid_price == 4999999.0
        assert ticker.ask_price == 5000001.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
