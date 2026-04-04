"""Tests for exchange_registers/register_ripio.py."""

import pytest

from bt_api_py.exchange_registers import register_ripio


class TestRegisterRipio:
    """Tests for Ripio registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_ripio is not None
