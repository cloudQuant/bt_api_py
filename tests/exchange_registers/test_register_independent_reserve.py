"""Tests for exchange_registers/register_independent_reserve.py."""


from bt_api_py.exchange_registers import register_independent_reserve


class TestRegisterIndependentReserve:
    """Tests for Independent Reserve registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_independent_reserve is not None
