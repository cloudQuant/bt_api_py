"""Tests for exchange_registers/register_coinspot.py."""


from bt_api_py.exchange_registers import register_coinspot


class TestRegisterCoinspot:
    """Tests for Coinspot registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_coinspot is not None
