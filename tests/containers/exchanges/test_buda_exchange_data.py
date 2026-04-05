"""Tests for BudaExchangeData container."""

from bt_api_py.containers.exchanges.buda_exchange_data import BudaExchangeData


class TestBudaExchangeData:
    """Tests for BudaExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BudaExchangeData()

        assert exchange.exchange_name == "buda"
