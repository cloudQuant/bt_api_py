"""Tests for BeQuantExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.bequant_exchange_data import BeQuantExchangeData


class TestBeQuantExchangeData:
    """Tests for BeQuantExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BeQuantExchangeData()

        assert exchange.exchange_name == "BEQUANT___SPOT"
