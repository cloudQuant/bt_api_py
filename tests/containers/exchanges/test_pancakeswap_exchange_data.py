"""Tests for PancakeswapExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.pancakeswap_exchange_data import PancakeSwapExchangeData


class TestPancakeSwapExchangeData:
    """Tests for PancakeSwapExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = PancakeSwapExchangeData()

        assert exchange.exchange_name == "pancakeswap"
