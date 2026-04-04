"""Tests for gateway/runtime.py."""

import pytest


class TestRuntime:
    """Tests for gateway runtime."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.gateway import runtime
        assert runtime is not None
