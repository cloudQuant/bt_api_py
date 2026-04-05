"""Tests for exchange_registers/register_bitunix.py."""

from bt_api_py.exchange_registers import register_bitunix


class TestRegisterBitunix:
    """Tests for Bitunix registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitunix is not None
