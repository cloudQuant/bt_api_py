"""Tests for exchange_registers/register_bitinka.py."""

import pytest

from bt_api_py.exchange_registers import register_bitinka


class TestRegisterBitinka:
    """Tests for Bitinka registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitinka is not None
