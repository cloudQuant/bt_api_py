"""Tests for exchange_registers/register_bingx.py."""

import pytest

from bt_api_py.exchange_registers import register_bingx


class TestRegisterBingx:
    """Tests for BingX registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_bingx is not None
