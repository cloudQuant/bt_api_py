"""Tests for LunoExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.luno_exchange_data import LunoExchangeData


class TestLunoExchangeData:
    """Tests for LunoExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = LunoExchangeData()

        assert exchange.exchange_name == "LUNO___SPOT"
