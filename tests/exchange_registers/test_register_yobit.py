"""Tests for exchange_registers/register_yobit.py."""


from bt_api_py.exchange_registers import register_yobit


class TestRegisterYobit:
    """Tests for YoBit registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_yobit is not None
