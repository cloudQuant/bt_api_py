"""Tests for exchange_registers/register_pancakeswap.py."""

from bt_api_py.exchange_registers import register_pancakeswap


class TestRegisterPancakeswap:
    """Tests for PancakeSwap registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_pancakeswap is not None
