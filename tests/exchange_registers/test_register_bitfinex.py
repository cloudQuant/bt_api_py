"""Tests for exchange_registers/register_bitfinex.py."""

import pytest

from bt_api_py.exchange_registers import register_bitfinex


class TestRegisterBitfinex:
    """Tests for Bitfinex registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitfinex is not None
