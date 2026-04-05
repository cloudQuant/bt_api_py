"""Tests for exchange_registers/register_bitvavo.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_bitvavo


class TestRegisterBitvavo:
    """Tests for Bitvavo registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitvavo is not None
