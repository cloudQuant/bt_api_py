"""Tests for UpbitTranslator."""

from bt_api_py.errors.upbit_translator import UpbitErrorTranslator


class TestUpbitErrorTranslator:
    """Tests for UpbitErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(UpbitErrorTranslator, "ERROR_MAP")
