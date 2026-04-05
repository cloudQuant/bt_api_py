"""Tests for exchange_registers/register_coincheck.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_coincheck


class TestRegisterCoincheck:
    """Tests for Coincheck registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_coincheck is not None
