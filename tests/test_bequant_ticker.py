"""Tests for BeQuant ticker data container."""

import pytest

from bt_api_py.containers.tickers.bequant_ticker import BeQuantRequestTickerData


class TestBeQuantRequestTickerData:
    """Tests for BeQuantRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "symbol": "BTCUSDT",
            "last": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
            "volume": 1000.0,
            "high": 51000.0,
            "low": 49000.0,
            "open": 49500.0,
        }
        ticker = BeQuantRequestTickerData(
            ticker_info, "BTCUSDT", "SPOT", has_been_json_encoded=True
        )

        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.exchange_name == "BEQUANT"
        assert ticker.has_been_json_encoded is True

    def test_init_with_json_string(self):
        """Test initialization with JSON string."""
        import json

        ticker_info = json.dumps(
            {
                "symbol": "BTCUSDT",
                "last": 50000.0,
            }
        )
        ticker = BeQuantRequestTickerData(
            ticker_info, "BTCUSDT", "SPOT", has_been_json_encoded=False
        )

        assert ticker.symbol_name == "BTCUSDT"
        assert ticker.has_been_json_encoded is False

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "symbol": "BTCUSDT",
            "last": 50000.0,
            "bid": 49999.0,
            "ask": 50001.0,
            "volume": 1000.0,
            "high": 51000.0,
            "low": 49000.0,
            "open": 49500.0,
        }
        ticker = BeQuantRequestTickerData(
            ticker_info, "BTCUSDT", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 50000.0
        assert ticker.bid_price == 49999.0
        assert ticker.ask_price == 50001.0
        assert ticker.volume_24h == 1000.0
        assert ticker.high_24h == 51000.0
        assert ticker.low_24h == 49000.0
        assert ticker.open_24h == 49500.0
        assert ticker.ticker_symbol_name == "BTCUSDT"

    def test_init_data_idempotent(self):
        """Test that init_data is idempotent."""
        ticker_info = {
            "symbol": "BTCUSDT",
            "last": 50000.0,
        }
        ticker = BeQuantRequestTickerData(
            ticker_info, "BTCUSDT", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()
        first_price = ticker.last_price

        ticker.init_data()

        assert ticker.last_price == first_price

    def test_init_data_missing_fields(self):
        """Test init_data with missing fields."""
        ticker_info = {"symbol": "BTCUSDT"}
        ticker = BeQuantRequestTickerData(
            ticker_info, "BTCUSDT", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price is None
        assert ticker.bid_price is None
        assert ticker.ask_price is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
