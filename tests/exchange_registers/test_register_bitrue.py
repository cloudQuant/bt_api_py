"""Tests for exchange_registers/register_bitrue.py."""


from bt_api_py.exchange_registers import register_bitrue


class TestRegisterBitrue:
    """Tests for Bitrue registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitrue is not None
