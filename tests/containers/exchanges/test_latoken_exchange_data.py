"""Tests for LatokenExchangeData container."""

from bt_api_py.containers.exchanges.latoken_exchange_data import LatokenExchangeData


class TestLatokenExchangeData:
    """Tests for LatokenExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = LatokenExchangeData()

        assert exchange.exchange_name == "LATOKEN___SPOT"
