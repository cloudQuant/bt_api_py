"""Tests for bt_api helper functions."""

from datetime import UTC, datetime

import pytest

from bt_api_py.bt_api import _calculate_time_delta, _parse_time
from bt_api_py.exceptions import DataParseError


class TestCalculateTimeDelta:
    """Tests for _calculate_time_delta function."""

    def test_valid_periods(self):
        """Test valid period strings."""
        from datetime import timedelta

        assert _calculate_time_delta("1m") == timedelta(minutes=1)
        assert _calculate_time_delta("3m") == timedelta(minutes=3)
        assert _calculate_time_delta("5m") == timedelta(minutes=5)
        assert _calculate_time_delta("15m") == timedelta(minutes=15)
        assert _calculate_time_delta("30m") == timedelta(minutes=30)
        assert _calculate_time_delta("1H") == timedelta(hours=1)
        assert _calculate_time_delta("1D") == timedelta(days=1)

    def test_invalid_period(self):
        """Test invalid period raises DataParseError."""
        with pytest.raises(DataParseError, match="Unsupported period"):
            _calculate_time_delta("2m")

    def test_invalid_period_format(self):
        """Test invalid period format raises DataParseError."""
        with pytest.raises(DataParseError, match="Unsupported period"):
            _calculate_time_delta("invalid")


class TestParseTime:
    """Tests for _parse_time function."""

    def test_parse_iso_string(self):
        """Test parsing ISO format string."""
        result = _parse_time("2024-01-15T10:30:00")

        assert result is not None
        assert result.tzinfo is not None

    def test_parse_iso_string_with_timezone(self):
        """Test parsing ISO format string with timezone."""
        result = _parse_time("2024-01-15T10:30:00+08:00")

        assert result is not None
        assert result.tzinfo is not None

    def test_parse_datetime_object(self):
        """Test parsing datetime object."""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = _parse_time(dt)

        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_parse_datetime_with_timezone(self):
        """Test parsing datetime object with timezone."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        result = _parse_time(dt)

        assert result is not None
        assert result.tzinfo is not None

    def test_parse_none(self):
        """Test parsing None returns None."""
        result = _parse_time(None)

        assert result is None

    def test_parse_invalid_string(self):
        """Test parsing invalid string raises DataParseError."""
        with pytest.raises(DataParseError, match="Invalid ISO time format"):
            _parse_time("not-a-date")

    def test_parse_unsupported_type(self):
        """Test parsing unsupported type raises DataParseError."""
        with pytest.raises(DataParseError, match="Unsupported time format"):
            _parse_time(12345)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
