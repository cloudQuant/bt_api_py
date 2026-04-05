"""Tests for SwyftxExchangeData container."""


from bt_api_py.containers.exchanges.swyftx_exchange_data import SwyftxExchangeData


class TestSwyftxExchangeData:
    """Tests for SwyftxExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = SwyftxExchangeData()

        assert exchange.exchange_name == "SWYFTX"
