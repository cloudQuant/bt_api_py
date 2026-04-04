"""Tests for BitflyerExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.bitflyer_exchange_data import BitflyerExchangeData


class TestBitflyerExchangeData:
    """Tests for BitflyerExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitflyerExchangeData()

        assert exchange.exchange_name == "bitflyer"
        assert "bitflyer.com" in exchange.rest_url
