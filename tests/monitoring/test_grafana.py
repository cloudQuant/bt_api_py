"""Tests for monitoring/grafana.py."""

import pytest


class TestGrafana:
    """Tests for Grafana integration."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.monitoring import grafana
        assert grafana is not None
