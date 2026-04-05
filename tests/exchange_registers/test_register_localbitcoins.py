"""Tests for exchange_registers/register_localbitcoins.py."""

from bt_api_py.exchange_registers import register_localbitcoins


class TestRegisterLocalbitcoins:
    """Tests for LocalBitcoins registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_localbitcoins is not None
