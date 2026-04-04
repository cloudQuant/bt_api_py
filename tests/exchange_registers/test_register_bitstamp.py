"""Tests for exchange_registers/register_bitstamp.py."""

import pytest

from bt_api_py.exchange_registers import register_bitstamp


class TestRegisterBitstamp:
    """Tests for Bitstamp registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bitstamp is not None
