"""Tests for FoxbitExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.foxbit_exchange_data import FoxbitExchangeData


class TestFoxbitExchangeData:
    """Tests for FoxbitExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = FoxbitExchangeData()

        assert exchange.exchange_name == "foxbit"
