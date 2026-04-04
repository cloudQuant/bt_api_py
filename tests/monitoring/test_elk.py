"""Tests for monitoring/elk.py."""

import pytest


class TestElk:
    """Tests for ELK integration."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.monitoring import elk
        assert elk is not None
