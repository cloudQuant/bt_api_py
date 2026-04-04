"""Tests for IbWebTranslator."""

from bt_api_py.errors.ib_web_translator import IBWebErrorTranslator


class TestIBWebErrorTranslator:
    """Tests for IBWebErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(IBWebErrorTranslator, "ERROR_MAP")
