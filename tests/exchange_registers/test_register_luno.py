"""Tests for exchange_registers/register_luno.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_luno


class TestRegisterLuno:
    """Tests for Luno registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_luno is not None
