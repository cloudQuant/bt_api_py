"""Tests for MercadoBitcoinExchangeData container."""

from bt_api_py.containers.exchanges.mercado_bitcoin_exchange_data import MercadoBitcoinExchangeData


class TestMercadoBitcoinExchangeData:
    """Tests for MercadoBitcoinExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = MercadoBitcoinExchangeData()

        assert exchange.exchange_name == "mercado_bitcoin"
