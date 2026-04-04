"""Tests for Valr ticker data container."""

import pytest

from bt_api_py.containers.tickers.valr_ticker import ValrRequestTickerData


class TestValrRequestTickerData:
    """Tests for ValrRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "symbol": "BTCZAR",
            "lastPrice": 500000.0,
            "bidPrice": 499999.0,
            "askPrice": 500001.0,
            "baseVolume": 10.5,
            "highPrice": 510000.0,
            "lowPrice": 490000.0,
        }
        ticker = ValrRequestTickerData(
            ticker_info, "BTCZAR", "SPOT", has_been_json_encoded=True
        )

        assert ticker.symbol_name == "BTCZAR"
        assert ticker.exchange_name == "VALR"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "symbol": "BTCZAR",
            "lastPrice": 500000.0,
            "bidPrice": 499999.0,
            "askPrice": 500001.0,
            "baseVolume": 10.5,
            "highPrice": 510000.0,
            "lowPrice": 490000.0,
        }
        ticker = ValrRequestTickerData(
            ticker_info, "BTCZAR", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 500000.0
        assert ticker.bid_price == 499999.0
        assert ticker.ask_price == 500001.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
