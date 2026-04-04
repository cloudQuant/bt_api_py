"""Tests for exchange_registers/register_cryptocom.py."""

import pytest

from bt_api_py.exchange_registers import register_cryptocom


class TestRegisterCryptocom:
    """Tests for Crypto.com registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_cryptocom is not None
