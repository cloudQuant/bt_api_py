"""Tests for exchange_registers/register_coindcx.py."""

import pytest

from bt_api_py.exchange_registers import register_coindcx


class TestRegisterCoindcx:
    """Tests for CoinDCX registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_coindcx is not None
