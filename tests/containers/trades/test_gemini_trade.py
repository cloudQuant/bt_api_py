"""Tests for GeminiRequestTradeData container."""

from bt_api_py.containers.trades.gemini_trade import GeminiRequestTradeData


class TestGeminiRequestTradeData:
    """Tests for GeminiRequestTradeData."""

    def test_init(self):
        """Test initialization."""
        trade = GeminiRequestTradeData({})

        assert trade.exchange_name == "GEMINI"

    def test_parse_rest_data(self):
        """Test parsing REST API data."""
        data = {
            "tid": "123456",
            "price": "50000.0",
            "amount": "1.0",
            "type": "buy",
            "timestampms": 1234567890000,
        }
        trade = GeminiRequestTradeData(data, symbol="BTCUSD", asset_type="SPOT")

        assert trade.trade_id == "123456"
        assert trade.price == 50000.0

    def test_to_dict(self):
        """Test to_dict."""
        trade = GeminiRequestTradeData({})
        result = trade.to_dict()

        assert result is not None

    def test_str_representation(self):
        """Test __str__ method."""
        trade = GeminiRequestTradeData({})
        result = str(trade)

        assert result is not None
