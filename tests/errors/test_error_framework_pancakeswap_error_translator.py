"""Tests for ErrorFrameworkPancakeswapErrorTranslator."""

from bt_api_py.errors.error_framework_pancakeswap_error_translator import PancakeSwapErrorTranslator


class TestPancakeSwapErrorTranslator:
    """Tests for PancakeSwapErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(PancakeSwapErrorTranslator, "ERROR_MAP")
