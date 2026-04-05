"""Tests for monitoring/system_metrics.py."""



class TestSystemMetrics:
    """Tests for system metrics."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.monitoring import system_metrics

        assert system_metrics is not None
