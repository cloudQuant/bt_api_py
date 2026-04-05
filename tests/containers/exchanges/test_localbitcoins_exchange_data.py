"""Tests for LocalbitcoinsExchangeData container."""

from bt_api_py.containers.exchanges.localbitcoins_exchange_data import LocalBitcoinsExchangeData


class TestLocalBitcoinsExchangeData:
    """Tests for LocalBitcoinsExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = LocalBitcoinsExchangeData()

        assert exchange.exchange_name == "LOCALBITCOINS___SPOT"
