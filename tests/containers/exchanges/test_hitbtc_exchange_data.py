"""Tests for HitbtcExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.hitbtc_exchange_data import HitBtcExchangeData


class TestHitBtcExchangeData:
    """Tests for HitBtcExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = HitBtcExchangeData()

        assert exchange.exchange_name == "HITBTC"
