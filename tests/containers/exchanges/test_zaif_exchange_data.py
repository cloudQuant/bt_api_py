"""Tests for ZaifExchangeData container."""

from bt_api_py.containers.exchanges.zaif_exchange_data import ZaifExchangeData


class TestZaifExchangeData:
    """Tests for ZaifExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = ZaifExchangeData()

        assert exchange.exchange_name == "ZAIF"
