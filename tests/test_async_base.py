"""Tests for AsyncBase class."""

import pytest

from bt_api_py.functions.async_base import AsyncBase


class TestAsyncBaseInit:
    """Tests for AsyncBase initialization."""

    def test_init_defaults(self):
        """Test default initialization."""
        async_base = AsyncBase()

        assert async_base.loop is not None
        assert async_base.keepalive_timeout == 30
        assert async_base.client_timeout == 5
        assert async_base.limit == 100
        assert async_base.session is None

        # Cleanup
        async_base.release()

    def test_init_with_proxy(self):
        """Test initialization with proxy."""
        async_base = AsyncBase(async_proxy="http://proxy:8080")

        assert async_base.async_proxy == "http://proxy:8080"

        # Cleanup
        async_base.release()


class TestAsyncBaseMethods:
    """Tests for AsyncBase methods."""

    def test_release(self):
        """Test release method."""
        async_base = AsyncBase()

        async_base.release()

        # Loop should be stopped
        assert async_base.loop is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
