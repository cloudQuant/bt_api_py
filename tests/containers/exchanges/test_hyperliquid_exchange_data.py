"""Tests for HyperliquidExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeData


class TestHyperliquidExchangeData:
    """Tests for HyperliquidExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = HyperliquidExchangeData()

        assert exchange.exchange_name == "hyperliquid_spot"
