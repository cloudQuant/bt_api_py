"""Tests for CoinbaseExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.coinbase_exchange_data import CoinbaseExchangeData


class TestCoinbaseExchangeData:
    """Tests for CoinbaseExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = CoinbaseExchangeData()

        assert exchange.exchange_name == "COINBASE___SPOT"
