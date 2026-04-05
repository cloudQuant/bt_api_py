"""Tests for BtcturkExchangeData container."""

from bt_api_py.containers.exchanges.btcturk_exchange_data import BTCTurkExchangeData


class TestBTCTurkExchangeData:
    """Tests for BTCTurkExchangeData."""

    def test_init(self):
        """Test initialization."""
        exchange = BTCTurkExchangeData()

        assert "BTCTURK" in exchange.exchange_name or "btcturk" in exchange.exchange_name.lower()
