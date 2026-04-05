"""Tests for exchange_registers/register_bequant.py."""

from bt_api_py.exchange_registers import register_bequant


class TestRegisterBequant:
    """Tests for Bequant registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bequant is not None
