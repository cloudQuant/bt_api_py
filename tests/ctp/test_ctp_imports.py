"""Tests for CTP module imports.

Note: CTP is a C++ extension module, tests verify module structure.
"""

from __future__ import annotations

import pytest


class TestCtpModule:
    """Tests for CTP module availability."""

    def test_ctp_constants_import(self):
        """Test ctp_constants can be imported."""
        try:
            from bt_api_py.ctp import ctp_constants

            assert ctp_constants is not None
        except ImportError:
            pytest.skip("CTP extension not available")

    def test_ctp_structs_common_import(self):
        """Test ctp_structs_common can be imported."""
        try:
            from bt_api_py.ctp import ctp_structs_common

            assert ctp_structs_common is not None
        except ImportError:
            pytest.skip("CTP extension not available")
