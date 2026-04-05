"""Tests for CryptocomExchangeData container."""


from bt_api_py.containers.exchanges.cryptocom_exchange_data import CryptoComExchangeData


class TestCryptoComExchangeData:
    """Tests for CryptoComExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = CryptoComExchangeData()

        assert exchange.exchange_name == "cryptocom"
