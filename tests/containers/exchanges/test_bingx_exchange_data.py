"""Tests for BingXExchangeData container."""


from bt_api_py.containers.exchanges.bingx_exchange_data import BingXExchangeData


class TestBingXExchangeData:
    """Tests for BingXExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BingXExchangeData()

        assert exchange.exchange_name == "bingx"
        assert "bingx.com" in exchange.rest_url
