"""Tests for BitstampExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bitstamp_exchange_data import BitstampExchangeData


class TestBitstampExchangeData:
    """Tests for BitstampExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitstampExchangeData()

        assert exchange.exchange_name == "BITSTAMP___SPOT"
