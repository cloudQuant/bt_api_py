"""Tests for BitgetErrorTranslator."""

from bt_api_py.errors.bitget_translator import BitgetErrorTranslator


class TestBitgetErrorTranslator:
    """Tests for BitgetErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(BitgetErrorTranslator, "ERROR_MAP")
