"""Tests for exchange_registers/register_dydx.py."""

import pytest

from bt_api_py.exchange_registers import register_dydx


class TestRegisterDydx:
    """Tests for dYdX registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_dydx is not None
