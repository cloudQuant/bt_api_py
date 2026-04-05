"""Tests for exchange_registers/register_coinbase.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_coinbase


class TestRegisterCoinbase:
    """Tests for Coinbase registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_coinbase is not None
