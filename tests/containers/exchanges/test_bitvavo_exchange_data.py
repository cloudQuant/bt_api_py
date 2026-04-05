"""Tests for BitvavoExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.bitvavo_exchange_data import BitvavoExchangeData


class TestBitvavoExchangeData:
    """Tests for BitvavoExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitvavoExchangeData()

        assert exchange.exchange_name == "bitvavo"
