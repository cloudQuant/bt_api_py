"""Tests for BudaRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.buda_ticker import BudaRequestTickerData


class TestBudaRequestTickerData:
    """Tests for BudaRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BudaRequestTickerData({}, symbol_name="BTC-CLP", asset_type="SPOT")

        assert ticker.exchange_name == "BUDA"
        assert ticker.symbol_name == "BTC-CLP"
        assert ticker.asset_type == "SPOT"
        assert ticker.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"ticker": {"last_price": ["50000.0", "CLP"]}}
        ticker = BudaRequestTickerData(
            data, symbol_name="BTC-CLP", asset_type="SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data - base class raises NotImplementedError."""
        ticker = BudaRequestTickerData(
            {}, symbol_name="BTC-CLP", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            ticker.get_all_data()

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = BudaRequestTickerData(
            {}, symbol_name="BTC-CLP", asset_type="SPOT", has_been_json_encoded=True
        )
        with pytest.raises(NotImplementedError):
            str(ticker)
