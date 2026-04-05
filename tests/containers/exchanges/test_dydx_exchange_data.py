"""Tests for DydxExchangeData container."""


from bt_api_py.containers.exchanges.dydx_exchange_data import DydxExchangeData


class TestDydxExchangeData:
    """Tests for DydxExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = DydxExchangeData()

        assert "Dydx" in exchange.exchange_name or "DYDX" in exchange.exchange_name
