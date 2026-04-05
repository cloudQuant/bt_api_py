"""Tests for BybitErrorTranslator."""


from bt_api_py.errors.bybit_translator import BybitErrorTranslator


class TestBybitErrorTranslator:
    """Tests for BybitErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(BybitErrorTranslator, "ERROR_MAP")
        assert len(BybitErrorTranslator.ERROR_MAP) > 0

    def test_error_map_contains_known_errors(self):
        """Test ERROR_MAP contains known Bybit error codes."""
        assert 10003 in BybitErrorTranslator.ERROR_MAP
        assert 110001 in BybitErrorTranslator.ERROR_MAP
