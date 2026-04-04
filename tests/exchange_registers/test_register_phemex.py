"""Tests for exchange_registers/register_phemex.py."""

import pytest

from bt_api_py.exchange_registers import register_phemex


class TestRegisterPhemex:
    """Tests for Phemex registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_phemex is not None
