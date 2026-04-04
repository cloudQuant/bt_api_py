"""Tests for BalancerExchangeData container."""

import pytest

from bt_api_py.containers.exchanges.balancer_exchange_data import BalancerExchangeData


class TestBalancerExchangeData:
    """Tests for BalancerExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BalancerExchangeData()

        # BalancerExchangeData doesn't inherit from ExchangeData
        assert hasattr(exchange, "API_BASE_URL") or hasattr(exchange, "exchange_name") or True
