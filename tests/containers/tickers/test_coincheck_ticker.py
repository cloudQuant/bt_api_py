"""Tests for CoincheckRequestTickerData container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.coincheck_ticker import CoincheckRequestTickerData


class TestCoincheckRequestTickerData:
    """Tests for CoincheckRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = CoincheckRequestTickerData({}, symbol_name="btc_jpy", asset_type="SPOT")

        assert ticker.exchange_name == "COINCHECK"
        assert ticker.symbol_name == "btc_jpy"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"last": "5000000", "bid": "4999000", "ask": "5001000"}
        ticker = CoincheckRequestTickerData(
            data, symbol_name="btc_jpy", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = CoincheckRequestTickerData(
            {}, symbol_name="btc_jpy", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = CoincheckRequestTickerData(
            {}, symbol_name="btc_jpy", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)
