"""Tests for BitfinexExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bitfinex_exchange_data import BitfinexExchangeData


class TestBitfinexExchangeData:
    """Tests for BitfinexExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitfinexExchangeData()

        assert exchange.exchange_name == "BITFINEX"
