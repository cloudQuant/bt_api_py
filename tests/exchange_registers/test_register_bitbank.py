"""Tests for exchange_registers/register_bitbank.py."""


from bt_api_py.exchange_registers import register_bitbank


class TestRegisterBitbank:
    """Tests for Bitbank registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitbank is not None
