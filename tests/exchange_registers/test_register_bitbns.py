"""Tests for exchange_registers/register_bitbns.py."""


from bt_api_py.exchange_registers import register_bitbns


class TestRegisterBitbns:
    """Tests for Bitbns registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitbns is not None
