"""Tests for exchange_registers/register_ib.py.

Note: Requires ib_exchange_data module.
"""

from __future__ import annotations

import pytest


class TestRegisterIb:
    """Tests for IB registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        try:
            from bt_api_py.exchange_registers import register_ib

            assert register_ib is not None
        except ImportError:
            pytest.skip("IB exchange data module not available")
