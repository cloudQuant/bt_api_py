"""Tests for HitBtcRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.hitbtc_ticker import HitBtcRequestTickerData


class TestHitBtcRequestTickerData:
    """Tests for HitBtcRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = HitBtcRequestTickerData({}, symbol_name="BTCUSD", asset_type="SPOT")

        assert ticker.exchange_name == "HITBTC"
        assert ticker.symbol_name == "BTCUSD"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {
            "symbol": "BTCUSD",
            "last": "50000.0",
            "bid": "49990.0",
            "ask": "50010.0",
            "high": "51000.0",
            "low": "49000.0",
        }
        ticker = HitBtcRequestTickerData(data, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.last_price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = HitBtcRequestTickerData({}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result["exchange_name"] == "HITBTC"
        assert result["symbol_name"] == "BTCUSD"

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = HitBtcRequestTickerData({}, symbol_name="BTCUSD", asset_type="SPOT", has_been_json_encoded=True)
        result = str(ticker)

        assert "HITBTC" in result
