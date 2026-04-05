"""Tests for KrakenExchangeData container."""


from bt_api_py.containers.exchanges.kraken_exchange_data import KrakenExchangeData


class TestKrakenExchangeData:
    """Tests for KrakenExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = KrakenExchangeData()

        assert exchange.exchange_name == "kraken"
