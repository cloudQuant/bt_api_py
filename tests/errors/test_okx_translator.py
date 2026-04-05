"""Tests for OkxTranslator."""

from __future__ import annotations

from bt_api_py.errors.okx_translator import OKXErrorTranslator


class TestOKXErrorTranslator:
    """Tests for OKXErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(OKXErrorTranslator, "ERROR_MAP")
