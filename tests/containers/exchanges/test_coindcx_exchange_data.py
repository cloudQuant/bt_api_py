"""Tests for CoindcxExchangeData container."""

from bt_api_py.containers.exchanges.coindcx_exchange_data import CoinDCXExchangeData


class TestCoinDCXExchangeData:
    """Tests for CoinDCXExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = CoinDCXExchangeData()

        assert exchange.exchange_name == "coindcx"
