"""Tests for PoloniexTickerData container."""

import pytest

from bt_api_py.containers.tickers.poloniex_ticker import PoloniexTickerData


class TestPoloniexTickerData:
    """Tests for PoloniexTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = PoloniexTickerData({}, symbol_name="BTC_USDT", asset_type="SPOT")

        assert ticker.exchange_name == "POLONIEX"
        assert ticker.symbol_name == "BTC_USDT"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        ticker = PoloniexTickerData({}, symbol_name="BTC_USDT", asset_type="SPOT")

        with pytest.raises(NotImplementedError):
            ticker.init_data()

    def test_get_all_data_raises_not_implemented(self):
        """Test get_all_data - calls init_data which raises NotImplementedError."""
        ticker = PoloniexTickerData({}, symbol_name="BTC_USDT", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation_raises_not_implemented(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = PoloniexTickerData({}, symbol_name="BTC_USDT", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
