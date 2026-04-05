"""Tests for exchange_registers/register_raydium.py."""

from bt_api_py.exchange_registers import register_raydium


class TestRegisterRaydium:
    """Tests for Raydium registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_raydium is not None
