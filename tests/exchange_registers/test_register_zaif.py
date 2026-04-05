"""Tests for exchange_registers/register_zaif.py."""

from bt_api_py.exchange_registers import register_zaif


class TestRegisterZaif:
    """Tests for Zaif registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_zaif is not None
