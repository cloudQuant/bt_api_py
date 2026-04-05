"""Tests for ZebpayRequestTickerData container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.zebpay_ticker import ZebpayRequestTickerData


class TestZebpayRequestTickerData:
    """Tests for ZebpayRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = ZebpayRequestTickerData({}, symbol_name="BTCINR", asset_type="SPOT")

        assert ticker.exchange_name == "ZEBPAY"
        assert ticker.symbol_name == "BTCINR"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "5000000.0", "buy": "4999000.0", "sell": "5001000.0"}
        ticker = ZebpayRequestTickerData(
            data, symbol_name="BTCINR", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = ZebpayRequestTickerData(
            {}, symbol_name="BTCINR", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = ZebpayRequestTickerData(
            {}, symbol_name="BTCINR", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)
