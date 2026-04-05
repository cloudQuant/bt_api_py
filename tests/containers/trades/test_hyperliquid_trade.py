"""Tests for HyperliquidSpotWssTradeData container."""


from bt_api_py.containers.trades.hyperliquid_trade import HyperliquidSpotWssTradeData


class TestHyperliquidSpotWssTradeData:
    """Tests for HyperliquidSpotWssTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = HyperliquidSpotWssTradeData({}, symbol_name="BTC", asset_type="SWAP")

        assert trade.exchange_name == "HYPERLIQUID"
        assert trade.symbol_name == "BTC"
        assert trade.asset_type == "SWAP"
        assert trade.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with trade info."""
        data = {
            "tid": "123456",
            "orderOid": "abc123",
            "side": "BUY",
            "px": "50000.0",
            "sz": "1.0",
            "time": 1234567890.0,
        }
        trade = HyperliquidSpotWssTradeData(
            data, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        trade.init_data()

        assert trade.trade_id == "123456"
        assert trade.side == "BUY"

    def test_get_all_data(self):
        """Test get_all_data."""
        trade = HyperliquidSpotWssTradeData(
            {}, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        result = trade.get_all_data()

        assert result["exchange_name"] == "HYPERLIQUID"
        assert result["symbol_name"] == "BTC"

    def test_str_representation(self):
        """Test __str__ method."""
        trade = HyperliquidSpotWssTradeData(
            {}, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        result = str(trade)

        assert "Hyperliquid" in result
