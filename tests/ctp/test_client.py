"""Tests for CTP client module.

Note: CTP is a C++ extension module.
"""

from __future__ import annotations

import pytest


class TestCtpClient:
    """Tests for CTP client."""

    def test_module_import(self):
        """Test client module can be imported."""
        try:
            from bt_api_py.ctp import client

            assert client is not None
        except ImportError:
            pytest.skip("CTP extension not available")
