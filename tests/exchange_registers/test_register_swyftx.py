"""Tests for exchange_registers/register_swyftx.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_swyftx


class TestRegisterSwyftx:
    """Tests for Swyftx registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_swyftx is not None
