"""Tests for RipioExchangeData container."""


from bt_api_py.containers.exchanges.ripio_exchange_data import RipioExchangeData


class TestRipioExchangeData:
    """Tests for RipioExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = RipioExchangeData()

        assert exchange.exchange_name == "ripio"
