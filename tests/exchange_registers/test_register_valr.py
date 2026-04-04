"""Tests for exchange_registers/register_valr.py."""

import pytest

from bt_api_py.exchange_registers import register_valr


class TestRegisterValr:
    """Tests for VALR registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_valr is not None
