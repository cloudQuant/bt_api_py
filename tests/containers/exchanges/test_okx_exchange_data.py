"""Tests for OkxExchangeData container."""


from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeData


class TestOkxExchangeData:
    """Tests for OkxExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = OkxExchangeData()

        assert "Okx" in exchange.exchange_name or "OKX" in exchange.exchange_name
