"""Tests for CowSwapExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.cow_swap_exchange_data import CowSwapExchangeData


class TestCowSwapExchangeData:
    """Tests for CowSwapExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = CowSwapExchangeData()

        assert exchange.exchange_name == "cow_swap"
        assert "cow.fi" in exchange.rest_url
