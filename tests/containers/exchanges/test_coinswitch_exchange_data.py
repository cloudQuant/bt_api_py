"""Tests for CoinSwitchExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.coinswitch_exchange_data import CoinSwitchExchangeData


class TestCoinSwitchExchangeData:
    """Tests for CoinSwitchExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = CoinSwitchExchangeData()

        assert exchange.exchange_name == "COINSWITCH___SPOT"
