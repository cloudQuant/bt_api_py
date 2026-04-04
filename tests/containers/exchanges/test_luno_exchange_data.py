"""Tests for LunoExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.luno_exchange_data import LunoExchangeData


class TestLunoExchangeData:
    """Tests for LunoExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = LunoExchangeData()

        assert exchange.exchange_name == "LUNO___SPOT"
