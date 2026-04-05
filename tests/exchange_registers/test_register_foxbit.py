"""Tests for exchange_registers/register_foxbit.py."""


from bt_api_py.exchange_registers import register_foxbit


class TestRegisterFoxbit:
    """Tests for Foxbit registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_foxbit is not None
