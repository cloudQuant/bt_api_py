"""Tests for ErrorFrameworkHitbtc."""

from __future__ import annotations

from bt_api_py.errors.error_framework_hitbtc import HitBtcErrorTranslator


class TestHitBtcErrorTranslator:
    """Tests for HitBtcErrorTranslator."""

    def test_error_map_exists(self):
        """Test ERROR_MAP is defined."""
        assert hasattr(HitBtcErrorTranslator, "ERROR_MAP")
