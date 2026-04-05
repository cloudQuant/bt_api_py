"""Tests for exchange_registers/register_bybit.py."""

from bt_api_py.exchange_registers import register_bybit


class TestRegisterBybit:
    """Tests for Bybit registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bybit is not None
