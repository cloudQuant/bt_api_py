"""Tests for exchange_registers/register_curve.py."""


from bt_api_py.exchange_registers import register_curve


class TestRegisterCurve:
    """Tests for Curve registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_curve is not None
