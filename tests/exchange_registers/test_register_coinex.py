"""Tests for exchange_registers/register_coinex.py."""


from bt_api_py.exchange_registers import register_coinex


class TestRegisterCoinex:
    """Tests for CoinEx registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_coinex is not None
