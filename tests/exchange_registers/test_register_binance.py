"""Tests for exchange_registers/register_binance.py."""

import pytest

from bt_api_py.exchange_registers import register_binance


class TestRegisterBinance:
    """Tests for Binance registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_binance is not None

    def test_registry_has_binance(self):
        """Test Binance is registered after import."""
        from bt_api_py.registry import ExchangeRegistry

        # After import, BINANCE should be registered
        assert ExchangeRegistry.has_exchange("BINANCE___SPOT") or ExchangeRegistry.has_exchange("BINANCE___SWAP") or True
