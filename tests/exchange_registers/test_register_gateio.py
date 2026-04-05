"""Tests for exchange_registers/register_gateio.py."""

from bt_api_py.exchange_registers import register_gateio


class TestRegisterGateio:
    """Tests for Gate.io registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_gateio is not None
