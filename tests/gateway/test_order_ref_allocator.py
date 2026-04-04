"""Tests for gateway/order_ref_allocator.py."""

import pytest


class TestOrderRefAllocator:
    """Tests for order ref allocator."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.gateway import order_ref_allocator
        assert order_ref_allocator is not None
