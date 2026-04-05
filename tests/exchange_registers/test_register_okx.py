"""Tests for exchange_registers/register_okx.py."""


from bt_api_py.exchange_registers import register_okx


class TestRegisterOkx:
    """Tests for OKX registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_okx is not None
