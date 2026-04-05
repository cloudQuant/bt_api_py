"""Tests for exchange_registers/register_poloniex.py."""

from bt_api_py.exchange_registers import register_poloniex


class TestRegisterPoloniex:
    """Tests for Poloniex registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_poloniex is not None
