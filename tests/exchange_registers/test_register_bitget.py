"""Tests for exchange_registers/register_bitget.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_bitget


class TestRegisterBitget:
    """Tests for Bitget registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitget is not None
