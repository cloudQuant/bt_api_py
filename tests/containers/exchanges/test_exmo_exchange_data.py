"""Tests for ExmoExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.exmo_exchange_data import ExmoExchangeData


class TestExmoExchangeData:
    """Tests for ExmoExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = ExmoExchangeData()

        assert exchange.exchange_name == "exmo"
