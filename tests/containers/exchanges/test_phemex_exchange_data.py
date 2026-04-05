"""Tests for PhemexExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.phemex_exchange_data import PhemexExchangeData


class TestPhemexExchangeData:
    """Tests for PhemexExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = PhemexExchangeData()

        assert exchange.exchange_name == "phemex"
