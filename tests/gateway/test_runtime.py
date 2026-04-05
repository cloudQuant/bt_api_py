"""Tests for gateway/runtime.py."""

from __future__ import annotations


class TestRuntime:
    """Tests for gateway runtime."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.gateway import runtime

        assert runtime is not None
