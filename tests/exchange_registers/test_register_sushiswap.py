"""Tests for exchange_registers/register_sushiswap.py."""

import pytest

from bt_api_py.exchange_registers import register_sushiswap


class TestRegisterSushiswap:
    """Tests for SushiSwap registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_sushiswap is not None
