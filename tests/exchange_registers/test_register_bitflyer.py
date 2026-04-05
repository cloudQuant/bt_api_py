"""Tests for exchange_registers/register_bitflyer.py."""

from bt_api_py.exchange_registers import register_bitflyer


class TestRegisterBitflyer:
    """Tests for Bitflyer registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitflyer is not None
