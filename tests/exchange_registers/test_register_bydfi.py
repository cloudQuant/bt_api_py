"""Tests for exchange_registers/register_bydfi.py."""

import pytest

from bt_api_py.exchange_registers import register_bydfi


class TestRegisterBydfi:
    """Tests for BYDFi registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bydfi is not None
