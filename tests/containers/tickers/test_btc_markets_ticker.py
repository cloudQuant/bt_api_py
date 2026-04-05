"""Tests for BtcMarketsRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.btc_markets_ticker import BtcMarketsRequestTickerData


class TestBtcMarketsRequestTickerData:
    """Tests for BtcMarketsRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BtcMarketsRequestTickerData({}, symbol_name="BTC-AUD", asset_type="SPOT")

        assert ticker.exchange_name == "BTC_MARKETS"
        assert ticker.symbol_name == "BTC-AUD"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"lastPrice": "50000.0", "bid": "49990.0", "ask": "50010.0"}
        ticker = BtcMarketsRequestTickerData(
            data, symbol_name="BTC-AUD", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = BtcMarketsRequestTickerData(
            {}, symbol_name="BTC-AUD", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = BtcMarketsRequestTickerData(
            {}, symbol_name="BTC-AUD", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)
