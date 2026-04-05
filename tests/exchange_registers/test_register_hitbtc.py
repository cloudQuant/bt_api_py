"""Tests for exchange_registers/register_hitbtc.py."""

from __future__ import annotations

from bt_api_py.exchange_registers import register_hitbtc


class TestRegisterHitbtc:
    """Tests for HitBTC registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_hitbtc is not None
