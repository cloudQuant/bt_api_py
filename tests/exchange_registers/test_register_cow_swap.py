"""Tests for exchange_registers/register_cowswap.py."""


from bt_api_py.exchange_registers import register_cow_swap


class TestRegisterCowSwap:
    """Tests for CoW Swap registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_cow_swap is not None
