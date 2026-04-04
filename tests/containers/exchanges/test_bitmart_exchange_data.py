"""Tests for BitmartExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bitmart_exchange_data import BitmartExchangeData


class TestBitmartExchangeData:
    """Tests for BitmartExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitmartExchangeData()

        assert exchange.exchange_name == "BITMART___SPOT"
