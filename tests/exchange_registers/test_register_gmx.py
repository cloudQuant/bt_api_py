"""Tests for exchange_registers/register_gmx.py."""

from bt_api_py.exchange_registers import register_gmx


class TestRegisterGmx:
    """Tests for GMX registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_gmx is not None
