"""Tests for BitinkaExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bitinka_exchange_data import BitinkaExchangeData


class TestBitinkaExchangeData:
    """Tests for BitinkaExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitinkaExchangeData()

        assert exchange.exchange_name == "bitinka"
