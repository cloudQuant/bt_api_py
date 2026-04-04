"""Tests for ErrorFrameworkMexc."""

from bt_api_py.errors.error_framework_mexc import MexcErrorTranslator


class TestMexcErrorTranslator:
    """Tests for MexcErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(MexcErrorTranslator, "ERROR_MAP")
