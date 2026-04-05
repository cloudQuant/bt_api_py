"""Tests for ValrRequestTickerData container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.tickers.valr_ticker import ValrRequestTickerData


class TestValrRequestTickerData:
    """Tests for ValrRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = ValrRequestTickerData({}, symbol_name="BTCZAR", asset_type="SPOT")

        assert ticker.exchange_name == "VALR"
        assert ticker.symbol_name == "BTCZAR"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"lastPrice": "500000.0", "bidPrice": "499900.0", "askPrice": "500100.0"}
        ticker = ValrRequestTickerData(
            data, symbol_name="BTCZAR", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = ValrRequestTickerData(
            {}, symbol_name="BTCZAR", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = ValrRequestTickerData(
            {}, symbol_name="BTCZAR", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)
