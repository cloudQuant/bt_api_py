"""Tests for BydfiExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.bydfi_exchange_data import BYDFiExchangeData


class TestBYDFiExchangeData:
    """Tests for BYDFiExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BYDFiExchangeData()

        assert exchange.exchange_name == "bydfi"
