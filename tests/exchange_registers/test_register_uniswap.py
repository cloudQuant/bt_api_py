"""Tests for exchange_registers/register_uniswap.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_uniswap


class TestRegisterUniswap:
    """Tests for Uniswap registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_uniswap is not None
