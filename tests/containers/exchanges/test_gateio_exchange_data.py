"""Tests for GateioExchangeData container."""

from bt_api_py.containers.exchanges.gateio_exchange_data import GateioExchangeData


class TestGateioExchangeData:
    """Tests for GateioExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = GateioExchangeData()

        assert exchange.exchange_name == "gateio"
