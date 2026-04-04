"""Tests for UpbitExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.upbit_exchange_data import UpbitExchangeData


class TestUpbitExchangeData:
    """Tests for UpbitExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = UpbitExchangeData()

        assert exchange.exchange_name == "UPBIT___SPOT"
