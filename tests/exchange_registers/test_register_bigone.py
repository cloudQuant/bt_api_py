"""Tests for exchange_registers/register_bigone.py."""


from bt_api_py.exchange_registers import register_bigone


class TestRegisterBigone:
    """Tests for BigONE registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bigone is not None
