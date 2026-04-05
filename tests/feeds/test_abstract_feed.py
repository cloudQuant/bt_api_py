"""Tests for feeds/abstract_feed.py."""

from bt_api_py.feeds.abstract_feed import AbstractVenueFeed, AsyncWrapperMixin


class TestAbstractVenueFeed:
    """Tests for AbstractVenueFeed protocol."""

    def test_protocol_exists(self):
        """Test protocol is defined."""
        assert AbstractVenueFeed is not None

    def test_protocol_is_runtime_checkable(self):
        """Test protocol is runtime checkable."""

        assert hasattr(AbstractVenueFeed, "__protocol_attrs__")


class TestAsyncWrapperMixin:
    """Tests for AsyncWrapperMixin."""

    def test_mixin_exists(self):
        """Test mixin is defined."""
        assert AsyncWrapperMixin is not None
