"""Tests for CoinoneRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.coinone_ticker import CoinoneRequestTickerData


class TestCoinoneRequestTickerData:
    """Tests for CoinoneRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = CoinoneRequestTickerData({}, symbol_name="BTC", asset_type="SPOT")

        assert ticker.exchange_name == "COINONE"
        assert ticker.symbol_name == "BTC"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "50000000", "best_bid": "49990000", "best_ask": "50010000"}
        ticker = CoinoneRequestTickerData(
            data, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = CoinoneRequestTickerData(
            {}, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = CoinoneRequestTickerData(
            {}, symbol_name="BTC", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)
