"""Tests for SushiswapExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.sushiswap_exchange_data import SushiSwapExchangeData


class TestSushiSwapExchangeData:
    """Tests for SushiSwapExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = SushiSwapExchangeData()

        assert exchange.API_BASE_URL == "https://api.sushi.com"
