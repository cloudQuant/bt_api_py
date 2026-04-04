"""Tests for KucoinTranslator."""

from bt_api_py.errors.kucoin_translator import KuCoinErrorTranslator


class TestKuCoinErrorTranslator:
    """Tests for KuCoinErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(KuCoinErrorTranslator, "ERROR_MAP")
