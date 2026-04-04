"""Tests for exchange_registers/register_satoshitango.py."""

import pytest

from bt_api_py.exchange_registers import register_satoshitango


class TestRegisterSatoshitango:
    """Tests for Satoshitango registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_satoshitango is not None
