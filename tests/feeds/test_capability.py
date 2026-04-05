"""Tests for feeds/capability.py."""


from bt_api_py.feeds.capability import Capability


class TestCapability:
    """Tests for Capability enum."""

    def test_capability_exists(self):
        """Test Capability enum is defined."""
        assert Capability is not None

    def test_market_capabilities(self):
        """Test market-related capabilities."""
        assert Capability.GET_TICK.value == "get_tick"
        assert Capability.GET_DEPTH.value == "get_depth"
        assert Capability.GET_KLINE.value == "get_kline"

    def test_trading_capabilities(self):
        """Test trading-related capabilities."""
        assert Capability.MAKE_ORDER.value == "make_order"
        assert Capability.CANCEL_ORDER.value == "cancel_order"

    def test_account_capabilities(self):
        """Test account-related capabilities."""
        assert Capability.GET_BALANCE.value == "get_balance"
        assert Capability.GET_POSITION.value == "get_position"

    def test_stream_capabilities(self):
        """Test stream capabilities."""
        assert Capability.MARKET_STREAM.value == "market_stream"
        assert Capability.ACCOUNT_STREAM.value == "account_stream"
