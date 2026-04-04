"""Tests for exchange_registers/register_btcturk.py."""

import pytest

from bt_api_py.exchange_registers import register_btcturk


class TestRegisterBtcturk:
    """Tests for BTCTurk registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_btcturk is not None
