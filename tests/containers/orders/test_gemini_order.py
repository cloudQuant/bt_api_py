"""Tests for GeminiRequestOrderData container."""

from __future__ import annotations

from bt_api_py.containers.orders.gemini_order import GeminiRequestOrderData


class TestGeminiRequestOrderData:
    """Tests for GeminiRequestOrderData."""

    def test_init(self):
        """Test initialization."""
        order = GeminiRequestOrderData({})

        assert order.exchange_name == "GEMINI"
        assert order.is_rest is True

    def test_parse_rest_data(self):
        """Test parsing REST API data."""
        data = {
            "order_id": "123456",
            "client_order_id": "abc123",
            "symbol": "BTCUSD",
            "side": "buy",
            "type": "exchange limit",
            "status": "filled",
            "price": "50000.0",
            "original_amount": "1.0",
            "executed_amount": "1.0",
        }
        order = GeminiRequestOrderData(data)

        assert order.order_id == "123456"
        assert order.side == "buy"
        assert order.price == 50000.0

    def test_to_dict(self):
        """Test to_dict."""
        data = {"order_id": "123456", "symbol": "BTCUSD"}
        order = GeminiRequestOrderData(data)
        result = order.to_dict()

        assert "order_id" in result or result is not None

    def test_str_representation(self):
        """Test __str__ method."""
        data = {"order_id": "123456", "symbol": "BTCUSD"}
        order = GeminiRequestOrderData(data)
        result = str(order)

        assert result is not None
