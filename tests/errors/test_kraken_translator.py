"""Tests for KrakenTranslator."""

from bt_api_py.errors.kraken_translator import KrakenErrorTranslator


class TestKrakenErrorTranslator:
    """Tests for KrakenErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(KrakenErrorTranslator, "ERROR_MAP")
