"""Tests for exchange_registers/register_bitmart.py."""


from bt_api_py.exchange_registers import register_bitmart


class TestRegisterBitmart:
    """Tests for Bitmart registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitmart is not None
