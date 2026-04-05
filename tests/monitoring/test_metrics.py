"""Tests for monitoring/metrics.py."""

from __future__ import annotations

import pytest

pytest.importorskip("psutil")

from bt_api_py.monitoring.metrics import MetricValue


class TestMetricValue:
    """Tests for MetricValue dataclass."""

    def test_init(self):
        """Test initialization."""
        metric = MetricValue(value=100.0)
        assert metric.value == 100.0
        assert metric.labels == {}

    def test_with_labels(self):
        """Test with labels."""
        metric = MetricValue(value=100.0, labels={"exchange": "binance"})
        assert metric.labels["exchange"] == "binance"
