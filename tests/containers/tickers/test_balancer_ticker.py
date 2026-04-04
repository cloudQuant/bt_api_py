"""Tests for BalancerRequestTickerData container."""

import pytest

from bt_api_py.containers.tickers.balancer_ticker import BalancerRequestTickerData


class TestBalancerRequestTickerData:
    """Tests for BalancerRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = BalancerRequestTickerData({}, symbol_name="BTC-ETH", asset_type="SPOT")

        assert ticker.exchange_name == "BALANCER"
        assert ticker.symbol_name == "BTC-ETH"
        assert ticker.asset_type == "SPOT"

    def test_init_data(self):
        """Test init_data with ticker info."""
        data = {"price": "50000.0"}
        ticker = BalancerRequestTickerData(data, symbol_name="BTC-ETH", asset_type="SPOT", has_been_json_encoded=True)
        ticker.init_data()

        assert ticker.has_been_init_data is True

    def test_get_all_data(self):
        """Test get_all_data."""
        ticker = BalancerRequestTickerData({}, symbol_name="BTC-ETH", asset_type="SPOT", has_been_json_encoded=True)
        result = ticker.get_all_data()

        assert result["exchange_name"] == "BALANCER"
        assert result["symbol_name"] == "BTC-ETH"

    def test_str_representation(self):
        """Test __str__ method - base class raises NotImplementedError."""
        ticker = BalancerRequestTickerData({}, symbol_name="BTC-ETH", asset_type="SPOT", has_been_json_encoded=True)
        with pytest.raises(NotImplementedError):
            str(ticker)
