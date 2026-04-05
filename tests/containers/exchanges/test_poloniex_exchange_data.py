"""Tests for PoloniexExchangeData container."""

from bt_api_py.containers.exchanges.poloniex_exchange_data import PoloniexExchangeData


class TestPoloniexExchangeData:
    """Tests for PoloniexExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = PoloniexExchangeData()

        assert exchange.exchange_name == "poloniex"
