"""Tests for HyperliquidRequestOrderData container."""


from bt_api_py.containers.orders.hyperliquid_order import HyperliquidRequestOrderData


class TestHyperliquidRequestOrderData:
    """Tests for HyperliquidRequestOrderData."""

    def test_init(self):
        """Test initialization."""
        order = HyperliquidRequestOrderData({}, symbol_name="BTC", asset_type="SWAP")

        assert order.exchange_name == "HYPERLIQUID"
        assert order.symbol_name == "BTC"
        assert order.asset_type == "SWAP"
        assert order.has_been_init_data is False

    def test_init_data(self):
        """Test init_data with order info."""
        data = {
            "statuses": [
                {
                    "resting": {
                        "oid": "123456",
                        "side": "BUY",
                        "type": "LIMIT",
                        "sz": "1.0",
                        "limit_px": "50000.0",
                    }
                }
            ]
        }
        order = HyperliquidRequestOrderData(
            data, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        order.init_data()

        assert order.order_id == "123456"
        assert order.side == "BUY"

    def test_get_all_data(self):
        """Test get_all_data."""
        order = HyperliquidRequestOrderData(
            {}, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        result = order.get_all_data()

        assert result["exchange_name"] == "HYPERLIQUID"
        assert result["symbol_name"] == "BTC"

    def test_str_representation(self):
        """Test __str__ method."""
        order = HyperliquidRequestOrderData(
            {}, symbol_name="BTC", asset_type="SWAP", has_been_json_encoded=True
        )
        result = str(order)

        assert "HyperliquidRequestOrderData" in result
