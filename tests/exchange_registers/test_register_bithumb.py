"""Tests for exchange_registers/register_bithumb.py."""


from bt_api_py.exchange_registers import register_bithumb


class TestRegisterBithumb:
    """Tests for Bithumb registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bithumb is not None
