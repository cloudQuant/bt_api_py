"""
Tests for AbstractVenueFeed protocol conformance validation
"""

import pytest

from bt_api_py.feeds.abstract_feed import (
    AbstractVenueFeed,
    AsyncWrapperMixin,
    check_protocol_compliance,
)


class _CompleteFeed(AsyncWrapperMixin):
    """A complete feed implementation for testing"""

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True

    def get_tick(self, symbol: str, extra_data=None, **kwargs):
        return {"symbol": symbol}

    def get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        return {"bids": [], "asks": []}

    def get_kline(self, symbol: str, period: str, count: int = 20, extra_data=None, **kwargs):
        return []

    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        offset: str = "open",
        post_only: bool = False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        return {"orderId": "123"}

    def cancel_order(self, symbol: str, order_id: str, extra_data=None, **kwargs):
        return True

    def cancel_all(self, symbol=None, extra_data=None, **kwargs):
        return True

    def query_order(self, symbol: str, order_id: str, extra_data=None, **kwargs):
        return {}

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        return []

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        return {}

    def get_account(self, symbol: str = "ALL", extra_data=None, **kwargs):
        return {}

    def get_position(self, symbol=None, extra_data=None, **kwargs):
        return {}

    @property
    def capabilities(self) -> set:
        return {"tick", "depth", "kline", "order"}


class _IncompleteFeed:
    """An incomplete feed missing required methods"""

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def is_connected(self) -> bool:
        return False


class _MinimalFeed(AsyncWrapperMixin):
    """Minimal feed with only sync methods (async from mixin)"""

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True

    def get_tick(self, symbol: str, extra_data=None, **kwargs):
        return None

    def get_depth(self, symbol: str, count: int = 20, extra_data=None, **kwargs):
        return None

    def get_kline(self, symbol: str, period: str, count: int = 20, extra_data=None, **kwargs):
        return None

    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        offset: str = "open",
        post_only: bool = False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        return None

    def cancel_order(self, symbol: str, order_id: str, extra_data=None, **kwargs):
        return None

    def cancel_all(self, symbol=None, extra_data=None, **kwargs):
        return None

    def query_order(self, symbol: str, order_id: str, extra_data=None, **kwargs):
        return None

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        return None

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        return None

    def get_account(self, symbol: str = "ALL", extra_data=None, **kwargs):
        return None

    def get_position(self, symbol=None, extra_data=None, **kwargs):
        return None

    @property
    def capabilities(self) -> set:
        return set()


class TestProtocolConformance:
    """Test check_protocol_compliance function"""

    def test_complete_feed_passes(self):
        assert check_protocol_compliance(_CompleteFeed) == []

    def test_incomplete_feed_fails(self):
        missing = check_protocol_compliance(_IncompleteFeed)
        assert len(missing) > 0
        assert "get_tick" in missing
        assert "make_order" in missing
        assert "cancel_order" in missing

    def test_minimal_feed_with_mixin_passes(self):
        missing = check_protocol_compliance(_MinimalFeed)
        assert missing == []

    def test_missing_capabilities_property(self):
        class NoCapabilitiesFeed(AsyncWrapperMixin):
            def connect(self):
                pass

            def disconnect(self):
                pass

            def is_connected(self):
                return True

            def get_tick(self, symbol, extra_data=None, **kwargs):
                pass

            def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
                pass

            def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
                pass

            def make_order(
                self,
                symbol,
                volume,
                price,
                order_type,
                offset="open",
                post_only=False,
                client_order_id=None,
                extra_data=None,
                **kwargs,
            ):
                pass

            def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
                pass

            def cancel_all(self, symbol=None, extra_data=None, **kwargs):
                pass

            def query_order(self, symbol, order_id, extra_data=None, **kwargs):
                pass

            def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
                pass

            def get_balance(self, symbol=None, extra_data=None, **kwargs):
                pass

            def get_account(self, symbol="ALL", extra_data=None, **kwargs):
                pass

            def get_position(self, symbol=None, extra_data=None, **kwargs):
                pass

        missing = check_protocol_compliance(NoCapabilitiesFeed)
        assert "capabilities" not in missing


class TestProtocolRuntimeCheck:
    """Test Protocol runtime_checkable behavior"""

    def test_complete_feed_is_instance_of_protocol(self):
        feed = _CompleteFeed()
        assert isinstance(feed, AbstractVenueFeed)

    def test_minimal_feed_is_instance_of_protocol(self):
        feed = _MinimalFeed()
        assert isinstance(feed, AbstractVenueFeed)


class TestAsyncWrapperMixin:
    """Test AsyncWrapperMixin behavior"""

    @pytest.mark.asyncio
    async def test_async_get_tick_delegates_to_sync(self):
        feed = _CompleteFeed()
        result = await feed.async_get_tick("BTCUSDT")
        assert result == {"symbol": "BTCUSDT"}

    @pytest.mark.asyncio
    async def test_async_get_depth_with_count(self):
        feed = _CompleteFeed()
        result = await feed.async_get_depth("BTCUSDT", count=5)
        assert result == {"bids": [], "asks": []}

    @pytest.mark.asyncio
    async def test_async_make_order_passes_all_params(self):
        feed = _CompleteFeed()
        result = await feed.async_make_order(
            symbol="BTCUSDT",
            volume=0.1,
            price=50000.0,
            order_type="limit",
            offset="open",
            post_only=True,
            client_order_id="test123",
        )
        assert result == {"orderId": "123"}

    @pytest.mark.asyncio
    async def test_async_cancel_order(self):
        feed = _CompleteFeed()
        result = await feed.async_cancel_order("BTCUSDT", "order123")
        assert result is True

    @pytest.mark.asyncio
    async def test_async_get_balance(self):
        feed = _CompleteFeed()
        result = await feed.async_get_balance()
        assert result == {}
