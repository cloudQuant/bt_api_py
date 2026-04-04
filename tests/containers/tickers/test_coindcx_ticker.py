"""Tests for CoinDCXRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.coindcx_ticker import CoinDCXRequestTickerData


class TestCoinDCXRequestTickerData:
    """Tests for CoinDCXRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = CoinDCXRequestTickerData({}, symbol_name="BTCINR", asset_type="SPOT")

        assert ticker.exchange_name == "COINDCX"
        assert ticker.symbol_name == "BTCINR"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last_price": "50000.0", "bid": "49990.0", "ask": "50010.0"}
        ticker = CoinDCXRequestTickerData(data, symbol_name="BTCINR", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = CoinDCXRequestTickerData({}, symbol_name="BTCINR", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = CoinDCXRequestTickerData({}, symbol_name="BTCINR", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
