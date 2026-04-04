"""Tests for utils/time.py."""

from datetime import datetime

import pytest

from bt_api_py.utils.time import convert_utc_timestamp


class TestConvertUtcTimestamp:
    """Tests for convert_utc_timestamp function."""

    def test_convert_millisecond_timestamp(self):
        """Test converting millisecond timestamp."""
        # 2024-01-01 00:00:00 UTC in milliseconds
        result = convert_utc_timestamp(1704067200000)
        assert result is not None
        assert result.year == 2024

    def test_convert_second_timestamp(self):
        """Test converting second timestamp."""
        # 2024-01-01 00:00:00 UTC in seconds
        result = convert_utc_timestamp(1704067200)
        assert result is not None
        assert result.year == 2024

    def test_convert_string_timestamp(self):
        """Test converting string timestamp."""
        result = convert_utc_timestamp("1704067200000")
        assert result is not None
        assert result.year == 2024

    def test_convert_none_returns_none(self):
        """Test that None input returns None."""
        assert convert_utc_timestamp(None) is None

    def test_convert_invalid_string_returns_none(self):
        """Test that invalid string returns None."""
        assert convert_utc_timestamp("invalid") is None
