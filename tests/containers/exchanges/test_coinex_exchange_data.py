"""Tests for CoinexExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.coinex_exchange_data import CoinExExchangeData


class TestCoinExExchangeData:
    """Tests for CoinExExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = CoinExExchangeData()

        assert exchange.exchange_name == "COINEX___SPOT"
