"""Tests for exchange_registers/register_coinswitch.py."""

from bt_api_py.exchange_registers import register_coinswitch


class TestRegisterCoinswitch:
    """Tests for Coinswitch registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_coinswitch is not None
