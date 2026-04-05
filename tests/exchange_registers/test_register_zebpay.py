"""Tests for exchange_registers/register_zebpay.py."""


from bt_api_py.exchange_registers import register_zebpay


class TestRegisterZebpay:
    """Tests for Zebpay registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_zebpay is not None
