"""Tests for BitsoExchangeData container."""


from bt_api_py.containers.exchanges.bitso_exchange_data import BitsoExchangeData


class TestBitsoExchangeData:
    """Tests for BitsoExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BitsoExchangeData()

        assert exchange.exchange_name == "BITSO___SPOT"
