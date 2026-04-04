"""Tests for BitrueExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bitrue_exchange_data import BitrueExchangeData


class TestBitrueExchangeData:
    """Tests for BitrueExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitrueExchangeData()

        assert exchange.exchange_name == "BITRUE___SPOT"
