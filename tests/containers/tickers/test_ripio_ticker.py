"""Tests for RipioRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.ripio_ticker import RipioRequestTickerData


class TestRipioRequestTickerData:
    """Tests for RipioRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = RipioRequestTickerData({}, symbol="BTCARS", asset_type="SPOT")

        assert ticker.symbol_name == "BTCARS"
        assert ticker.asset_type == "SPOT"
        assert ticker.last_price is None

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = RipioRequestTickerData(
            {}, symbol="BTCARS", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = RipioRequestTickerData(
            {}, symbol="BTCARS", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(ticker)

        assert "Ripio" in result or "ripio" in result
