"""Tests for CoinSpotRequestTickerData container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.coinspot_ticker import CoinSpotRequestTickerData


class TestCoinSpotRequestTickerData:
    """Tests for CoinSpotRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = CoinSpotRequestTickerData({}, symbol_name="BTC", asset_type="SPOT")

        assert ticker.exchange_name == "COINSPOT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "50000.0", "bid": "49990.0", "ask": "50010.0"}
        ticker = CoinSpotRequestTickerData(
            data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = CoinSpotRequestTickerData(
            {}, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = CoinSpotRequestTickerData(
            {}, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)
