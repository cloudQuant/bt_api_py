"""Tests for BithumbExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.bithumb_exchange_data import BithumbExchangeData


class TestBithumbExchangeData:
    """Tests for BithumbExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BithumbExchangeData()

        assert exchange.exchange_name == "bithumb"
