"""Tests for exchange_registers/register_wazirx.py."""

import pytest

from bt_api_py.exchange_registers import register_wazirx


class TestRegisterWazirx:
    """Tests for WazirX registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_wazirx is not None
