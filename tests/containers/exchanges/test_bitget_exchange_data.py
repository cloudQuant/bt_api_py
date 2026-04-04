"""Tests for BitgetExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bitget_exchange_data import BitgetExchangeData


class TestBitgetExchangeData:
    """Tests for BitgetExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitgetExchangeData()

        assert exchange.exchange_name == "bitgetSpot"
