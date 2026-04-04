"""Tests for ErrorFrameworkCryptocom."""

from bt_api_py.errors.error_framework_cryptocom import CryptoComErrorTranslator


class TestCryptoComErrorTranslator:
    """Tests for CryptoComErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(CryptoComErrorTranslator, "ERROR_MAP")
