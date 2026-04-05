"""Tests for exchange_registers/register_bitso.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_bitso


class TestRegisterBitso:
    """Tests for Bitso registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitso is not None
