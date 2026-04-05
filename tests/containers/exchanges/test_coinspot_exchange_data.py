"""Tests for CoinspotExchangeData container."""


from bt_api_py.containers.exchanges.coinspot_exchange_data import CoinSpotExchangeData


class TestCoinSpotExchangeData:
    """Tests for CoinSpotExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = CoinSpotExchangeData()

        assert exchange.exchange_name == "COINSPOT___SPOT"
