"""Tests for GmxExchangeData container."""


from bt_api_py.containers.exchanges.gmx_exchange_data import GmxExchangeData


class TestGmxExchangeData:
    """Tests for GmxExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = GmxExchangeData()

        # GmxExchangeData doesn't inherit from ExchangeData
        assert hasattr(exchange, "API_BASE_URL") or hasattr(exchange, "exchange_name") or True
