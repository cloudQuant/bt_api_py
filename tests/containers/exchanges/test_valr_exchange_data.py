"""Tests for ValrExchangeData container."""

from bt_api_py.containers.exchanges.valr_exchange_data import ValrExchangeData


class TestValrExchangeData:
    """Tests for ValrExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = ValrExchangeData()

        assert exchange.exchange_name == "VALR"
