"""Tests for exchange_registers/register_ctp.py."""

import pytest


class TestRegisterCtp:
    """Tests for CTP registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        try:
            from bt_api_py.exchange_registers import register_ctp
            assert register_ctp is not None
        except ImportError:
            pytest.skip("CTP module not available")
