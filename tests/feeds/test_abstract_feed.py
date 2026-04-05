"""Tests for feeds/abstract_feed.py."""

from __future__ import annotations

from bt_api_py.feeds.abstract_feed import AbstractVenueFeed, AsyncWrapperMixin


class TestAbstractVenueFeed:
    """Tests for AbstractVenueFeed protocol."""

    def test_protocol_exists(self):
        """Test protocol is defined."""
        assert AbstractVenueFeed is not None

    def test_protocol_is_runtime_checkable(self):
        """Test protocol is runtime checkable."""
        assert getattr(AbstractVenueFeed, "_is_protocol", False) is True
        assert getattr(AbstractVenueFeed, "_is_runtime_protocol", False) is True


class TestAsyncWrapperMixin:
    """Tests for AsyncWrapperMixin."""

    def test_mixin_exists(self):
        """Test mixin is defined."""
        assert AsyncWrapperMixin is not None
