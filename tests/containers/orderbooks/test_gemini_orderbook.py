"""Tests for GeminiRequestOrderBookData container."""


from bt_api_py.containers.orderbooks.gemini_orderbook import GeminiRequestOrderBookData


class TestGeminiRequestOrderBookData:
    """Tests for GeminiRequestOrderBookData."""

    def test_init(self):
        """Test initialization."""
        orderbook = GeminiRequestOrderBookData({}, symbol="BTCUSD", asset_type="SPOT")

        assert orderbook.symbol == "BTCUSD"
        assert orderbook.asset_type == "SPOT"
        assert orderbook.bids == []
        assert orderbook.asks == []

    def test_parse_rest_data(self):
        """Test parsing REST data."""
        data = {
            "bids": [{"price": "50000.0", "amount": "1.0"}],
            "asks": [{"price": "50010.0", "amount": "1.0"}],
        }
        orderbook = GeminiRequestOrderBookData(data, symbol="BTCUSD", asset_type="SPOT")

        assert len(orderbook.bids) == 1
        assert len(orderbook.asks) == 1
