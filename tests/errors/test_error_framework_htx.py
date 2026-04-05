"""Tests for ErrorFrameworkHtx."""

from __future__ import annotations

from bt_api_py.errors.error_framework_htx import HtxErrorTranslator


class TestHtxErrorTranslator:
    """Tests for HtxErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(HtxErrorTranslator, "ERROR_MAP")
