"""Tests for exchange_registers/register_hyperliquid.py.

Note: Requires eth_account dependency.
"""

from __future__ import annotations

import pytest

pytest.importorskip("eth_account")

from bt_api_py.exchange_registers import register_hyperliquid


class TestRegisterHyperliquid:
    """Tests for Hyperliquid registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_hyperliquid is not None
