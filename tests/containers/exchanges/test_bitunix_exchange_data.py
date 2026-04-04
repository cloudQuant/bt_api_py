"""Tests for BitunixExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bitunix_exchange_data import BitunixExchangeData


class TestBitunixExchangeData:
    """Tests for BitunixExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitunixExchangeData()

        assert exchange.exchange_name == "bitunix"
