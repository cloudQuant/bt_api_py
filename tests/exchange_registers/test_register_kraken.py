"""Tests for exchange_registers/register_kraken.py."""

import pytest

from bt_api_py.exchange_registers import register_kraken


class TestRegisterKraken:
    """Tests for Kraken registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_kraken is not None
