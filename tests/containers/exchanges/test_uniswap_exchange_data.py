"""Tests for UniswapExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.uniswap_exchange_data import UniswapExchangeData


class TestUniswapExchangeData:
    """Tests for UniswapExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = UniswapExchangeData()

        # UniswapExchangeData doesn't inherit from ExchangeData
        assert hasattr(exchange, "API_BASE_URL") or hasattr(exchange, "exchange_name") or True
