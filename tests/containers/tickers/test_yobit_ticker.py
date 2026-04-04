"""Tests for YobitRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.yobit_ticker import YobitRequestTickerData


class TestYobitRequestTickerData:
    """Tests for YobitRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = YobitRequestTickerData({}, symbol_name="BTC_USD", asset_type="SPOT")

        assert ticker.exchange_name == "YOBIT"
        assert ticker.symbol_name == "BTC_USD"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "50000.0", "buy": "49990.0", "sell": "50010.0"}
        ticker = YobitRequestTickerData(data, symbol_name="BTC_USD", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = YobitRequestTickerData({}, symbol_name="BTC_USD", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = YobitRequestTickerData({}, symbol_name="BTC_USD", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
