"""Tests for exchange_registers/register_btc_markets.py."""

import pytest

from bt_api_py.exchange_registers import register_btc_markets


class TestRegisterBtcMarkets:
    """Tests for BTC Markets registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_btc_markets is not None
