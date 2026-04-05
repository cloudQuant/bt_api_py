"""Tests for MexcExchangeData container."""


from bt_api_py.containers.exchanges.mexc_exchange_data import MexcExchangeData


class TestMexcExchangeData:
    """Tests for MexcExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = MexcExchangeData()

        assert exchange.exchange_name == "mexc"
