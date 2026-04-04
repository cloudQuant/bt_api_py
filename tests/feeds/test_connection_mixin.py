"""Tests for feeds/connection_mixin.py."""

import pytest

from bt_api_py.feeds.connection_mixin import ConnectionMixin


class TestConnectionMixin:
    """Tests for ConnectionMixin."""

    def test_mixin_exists(self):
        """Test ConnectionMixin is defined."""
        assert ConnectionMixin is not None
