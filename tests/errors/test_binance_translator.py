"""Tests for BinanceErrorTranslator."""


from bt_api_py.errors.binance_translator import BinanceErrorTranslator


class TestBinanceErrorTranslator:
    """Tests for BinanceErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(BinanceErrorTranslator, "ERROR_MAP")
        assert len(BinanceErrorTranslator.ERROR_MAP) > 0

    def test_error_map_contains_known_errors(self):
        """Test ERROR_MAP contains known Binance error codes."""
        assert -1000 in BinanceErrorTranslator.ERROR_MAP
        assert -2010 in BinanceErrorTranslator.ERROR_MAP
