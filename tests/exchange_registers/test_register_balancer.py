"""Tests for exchange_registers/register_balancer.py."""


from bt_api_py.exchange_registers import register_balancer


class TestRegisterBalancer:
    """Tests for Balancer registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_balancer is not None
