"""Tests for monitoring/exchange_health.py."""

from __future__ import annotations


class TestExchangeHealth:
    """Tests for exchange health monitoring."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.monitoring import exchange_health

        assert exchange_health is not None
