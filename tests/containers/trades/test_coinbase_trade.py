"""Tests for CoinbaseTradeData container."""

from bt_api_py.containers.trades.coinbase_trade import CoinbaseTradeData


class TestCoinbaseTradeData:
    """Tests for CoinbaseTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = CoinbaseTradeData({}, symbol_name="BTC-USD", asset_type="SPOT")

        assert trade.exchange_name == "COINBASE"
        assert trade.symbol_name == "BTC-USD"
        assert trade.asset_type == "SPOT"
        assert trade.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with trade info."""
        data = {
            "entry_id": "123456",
            "order_id": "abc123",
            "product_id": "BTC-USD",
            "side": "buy",
            "price": "50000.0",
            "size": "1.0",
            "commission": "0.01",
        }
        trade = CoinbaseTradeData(
            data, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )
        trade.init_data()

        assert trade.trade_id == "123456"
        assert trade.side == "buy"
        assert trade.price == 50000.0

    def test_get_all_data(self):
        """Test get_all_data."""
        trade = CoinbaseTradeData(
            {}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = trade.get_all_data()

        assert result["exchange_name"] == "COINBASE"
        assert result["symbol_name"] == "BTC-USD"

    def test_str_representation(self):
        """Test __str__ method."""
        trade = CoinbaseTradeData(
            {}, symbol_name="BTC-USD", asset_type="SPOT", has_been_json_encoded=True
        )
        result = str(trade)

        assert "COINBASE" in result
