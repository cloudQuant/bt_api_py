"""Tests for exchange_registers/register_buda.py."""


from bt_api_py.exchange_registers import register_buda


class TestRegisterBuda:
    """Tests for Buda registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_buda is not None
