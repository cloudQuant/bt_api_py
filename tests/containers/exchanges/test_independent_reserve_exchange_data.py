"""Tests for IndependentReserveExchangeData container."""

from bt_api_py.containers.exchanges.independent_reserve_exchange_data import (
    IndependentReserveExchangeData,
)


class TestIndependentReserveExchangeData:
    """Tests for IndependentReserveExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = IndependentReserveExchangeData()

        assert exchange.exchange_name == "INDEPENDENT_RESERVE___SPOT"
