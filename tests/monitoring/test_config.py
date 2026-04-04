"""Tests for monitoring/config.py."""

import pytest

from bt_api_py.monitoring.config import MonitoringConfig


class TestMonitoringConfig:
    """Tests for MonitoringConfig."""

    def test_defaults(self):
        """Test default values."""
        config = MonitoringConfig()
        assert config.metrics_collection_interval == 5.0
        assert config.prometheus_port == 8080
        assert config.log_level == "INFO"
