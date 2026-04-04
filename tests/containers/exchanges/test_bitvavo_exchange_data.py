"""Tests for BitvavoExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bitvavo_exchange_data import BitvavoExchangeData


class TestBitvavoExchangeData:
    """Tests for BitvavoExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitvavoExchangeData()

        assert exchange.exchange_name == "bitvavo"
