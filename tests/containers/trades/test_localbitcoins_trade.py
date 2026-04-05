"""Tests for LocalBitcoinsSpotWssTradeData container."""


from bt_api_py.containers.trades.localbitcoins_trade import LocalBitcoinsSpotWssTradeData


class TestLocalBitcoinsSpotWssTradeData:
    """Tests for LocalBitcoinsSpotWssTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = LocalBitcoinsSpotWssTradeData({}, symbol_name="BTC_USD", asset_type="SPOT")

        assert trade.exchange_name == "LOCALBITCOINS"
        assert trade.symbol_name == "BTC_USD"
        assert trade.asset_type == "SPOT"
