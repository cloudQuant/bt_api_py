"""Tests for monitoring/collector.py."""

from __future__ import annotations


class TestCollector:
    """Tests for monitoring collector."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.monitoring import collector

        assert collector is not None
