"""Tests for exchange_registers/register_latoken.py."""

import pytest

from bt_api_py.exchange_registers import register_latoken


class TestRegisterLatoken:
    """Tests for Latoken registration module."""

    def test_module_imports(self):
        """Test module can be imported."""
        assert register_latoken is not None
