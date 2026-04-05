"""Tests for gateway/process.py."""

from __future__ import annotations


class TestProcess:
    """Tests for gateway process."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.gateway import process

        assert process is not None
