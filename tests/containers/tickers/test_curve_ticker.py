"""Tests for CurveRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.curve_ticker import CurveRequestTickerData


class TestCurveRequestTickerData:
    """Tests for CurveRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = CurveRequestTickerData({}, symbol_name="USDC-USDT", asset_type="SPOT")

        assert ticker.exchange_name == "CURVE"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"virtual_price": "1.05"}
        ticker = CurveRequestTickerData(data, symbol_name="USDC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = CurveRequestTickerData({}, symbol_name="USDC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result == {}

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = CurveRequestTickerData({}, symbol_name="USDC-USDT", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
