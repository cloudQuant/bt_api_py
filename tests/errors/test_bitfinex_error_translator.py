"""Tests for BitfinexErrorTranslator."""


from bt_api_py.errors.bitfinex_error_translator import BitfinexErrorTranslator


class TestBitfinexErrorTranslator:
    """Tests for BitfinexErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(BitfinexErrorTranslator, "ERROR_MAP")
