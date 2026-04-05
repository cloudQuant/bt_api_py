"""Tests for exchange_registers/register_mercado_bitcoin.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_mercado_bitcoin


class TestRegisterMercadoBitcoin:
    """Tests for Mercado Bitcoin registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_mercado_bitcoin is not None
