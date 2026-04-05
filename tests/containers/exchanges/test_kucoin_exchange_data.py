"""Tests for KucoinExchangeData container."""

from __future__ import annotations

from bt_api_py.containers.exchanges.kucoin_exchange_data import KuCoinExchangeData


class TestKuCoinExchangeData:
    """Tests for KuCoinExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = KuCoinExchangeData()

        assert exchange.exchange_name == "KUCOIN___SPOT"
