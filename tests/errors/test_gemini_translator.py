"""Tests for GeminiTranslator."""

from bt_api_py.errors.gemini_translator import GeminiErrorTranslator


class TestGeminiErrorTranslator:
    """Tests for GeminiErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(GeminiErrorTranslator, "ERROR_MAP")
