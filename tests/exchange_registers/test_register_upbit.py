"""Tests for exchange_registers/register_upbit.py."""

import pytest

from bt_api_py.exchange_registers import register_upbit


class TestRegisterUpbit:
    """Tests for Upbit registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_upbit is not None
