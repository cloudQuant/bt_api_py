"""Tests for exchange_registers/register_coinone.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_coinone


class TestRegisterCoinone:
    """Tests for Coinone registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_coinone is not None
