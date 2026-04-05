"""Tests for utils/hyperliquid_types.py."""


from bt_api_py.utils import hyperliquid_types


class TestHyperliquidTypes:
    """Tests for hyperliquid_types constants."""

    def test_order_types(self):
        """Test order type constants."""
        assert hyperliquid_types.LIMIT_ORDER == "limit"
        assert hyperliquid_types.MARKET_ORDER == "market"
        assert hyperliquid_types.TRIGGER_ORDER == "trigger"

    def test_time_in_force(self):
        """Test time in force constants."""
        assert hyperliquid_types.TIF_GTC == "Gtc"
        assert hyperliquid_types.TIF_IOC == "Ioc"
        assert hyperliquid_types.TIF_POST_ONLY == "PostOnly"

    def test_order_sides(self):
        """Test order side constants."""
        assert hyperliquid_types.SIDE_BUY is True
        assert hyperliquid_types.SIDE_SELL is False

    def test_order_status(self):
        """Test order status constants."""
        assert hyperliquid_types.STATUS_NEW == "NEW"
        assert hyperliquid_types.STATUS_FILLED == "FILLED"
        assert hyperliquid_types.STATUS_CANCELED == "CANCELED"
