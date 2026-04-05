"""Tests for BybitExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.bybit_exchange_data import BybitExchangeData


class TestBybitExchangeData:
    """Tests for BybitExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BybitExchangeData()

        assert exchange.exchange_name == "bybit"
