"""Tests for CoinbaseTickerData container."""

import pytest

from bt_api_py.containers.tickers.coinbase_ticker import CoinbaseTickerData


class TestCoinbaseTickerData:
    """Tests for CoinbaseTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = CoinbaseTickerData({}, symbol_name="BTC-USD", asset_type="SPOT")

        assert ticker.exchange_name == "COINBASE"
        assert ticker.symbol_name == "BTC-USD"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {
            "product_id": "BTC-USD",
            "last_trade": "50000.00",
            "best_bid": "49990.00",
            "best_ask": "50010.00",
            "volume_24h": "1000.0",
        }
        ticker = CoinbaseTickerData(data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = CoinbaseTickerData({}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result["exchange_name"] == "COINBASE"
        assert result["symbol_name"] == "BTC-USD"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = CoinbaseTickerData({}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True)
        result = str(ticker)

        assert "COINBASE" in result
