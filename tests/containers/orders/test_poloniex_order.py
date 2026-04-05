"""Tests for PoloniexOrderData container."""

import pytest

from bt_api_py.containers.orders.poloniex_order import PoloniexOrderData


class TestPoloniexOrderData:
    """Tests for PoloniexOrderData."""

    def test_init(self):
        """Test initialization."""
        order = PoloniexOrderData({}, symbol_name="BTC_USDT", asset_type="SPOT")

        assert order.exchange_name == "POLONIEX"
        assert order.symbol_name == "BTC_USDT"
        assert order.asset_type == "SPOT"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        order = PoloniexOrderData({}, symbol_name="BTC_USDT", asset_type="SPOT")

        with pytest.raises(NotImplementedError):
            order.init_data()

    def test_get_exchange_name(self):
        """Test get_exchange_name."""
        order = PoloniexOrderData({}, symbol_name="BTC_USDT", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin
        order._initialized = True
        assert order.get_exchange_name() == "POLONIEX"

    def test_get_symbol_name(self):
        """Test get_symbol_name."""
        order = PoloniexOrderData({}, symbol_name="BTC_USDT", asset_type="SPOT")
        # Set _initialized to prevent AutoInitMixin
        order._initialized = True
        assert order.get_symbol_name() == "BTC_USDT"

    def test_get_all_data(self):
        """Test get_all_data."""
        order = PoloniexOrderData(
            {}, symbol_name="BTC_USDT", asset_type="SPOT", has_been_json_encoded=True
        )
        # Set _initialized to prevent AutoInitMixin
        order._initialized = True
        result = order.get_all_data()

        assert result["exchange_name"] == "POLONIEX"
        assert result["symbol_name"] == "BTC_USDT"
