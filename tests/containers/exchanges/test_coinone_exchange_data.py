"""Tests for CoinoneExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.coinone_exchange_data import CoinoneExchangeData


class TestCoinoneExchangeData:
    """Tests for CoinoneExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = CoinoneExchangeData()

        assert exchange.exchange_name == "COINONE___SPOT"
