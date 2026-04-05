"""Tests for WazirxExchangeData container."""


from bt_api_py.containers.exchanges.wazirx_exchange_data import WazirxExchangeData


class TestWazirxExchangeData:
    """Tests for WazirxExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = WazirxExchangeData()

        assert exchange.exchange_name == "WAZIRX"
