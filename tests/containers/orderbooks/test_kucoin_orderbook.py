"""Tests for KuCoinOrderBookData container."""

from __future__ import annotations

import pytest

from bt_api_py.containers.orderbooks.kucoin_orderbook import KuCoinOrderBookData


class TestKuCoinOrderBookData:
    """Tests for KuCoinOrderBookData."""

    def test_init(self):
        """Test initialization."""
        orderbook = KuCoinOrderBookData({}, symbol_name="BTC-USDT", asset_type="SPOT")

        assert orderbook.exchange_name == "KUCOIN"
        assert orderbook.symbol_name == "BTC-USDT"
        assert orderbook.asset_type == "SPOT"
        assert orderbook.has_been_init_data is False

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        orderbook = KuCoinOrderBookData(
            {}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )

        with pytest.raises(NotImplementedError):
            orderbook.init_data()

    def test_get_all_data(self):
        """Test get_all_data method - raises NotImplementedError via init_data."""
        orderbook = KuCoinOrderBookData(
            {}, symbol_name="BTC-USDT", asset_type="SPOT", has_been_json_encoded=True
        )

        with pytest.raises(NotImplementedError):
            orderbook.get_all_data()
