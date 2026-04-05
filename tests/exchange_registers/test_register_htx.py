"""Tests for exchange_registers/register_htx.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_htx


class TestRegisterHtx:
    """Tests for HTX registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_htx is not None
