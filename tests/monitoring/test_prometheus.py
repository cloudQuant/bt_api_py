"""Tests for monitoring/prometheus.py."""

import pytest


class TestPrometheus:
    """Tests for Prometheus integration."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.monitoring import prometheus
        assert prometheus is not None
