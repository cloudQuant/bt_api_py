"""Tests for exchange_registers/register_mexc.py."""

from bt_api_py.exchange_registers import register_mexc


class TestRegisterMexc:
    """Tests for MEXC registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_mexc is not None
