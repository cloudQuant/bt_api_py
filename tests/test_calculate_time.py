"""Tests for calculate_time module."""

from __future__ import annotations

from datetime import datetime

import pytest

from bt_api_py._compat import UTC
from bt_api_py.functions.calculate_time import (
    convert_utc_local_datetime,
    datetime2str,
    datetime2timestamp,
    get_string_tz_time,
    get_utc_time,
    str2datetime,
    timestamp2datetime,
)


class TestGetUtcTime:
    """Tests for get_utc_time function."""

    def test_returns_string(self):
        """Test that function returns a string."""
        result = get_utc_time()

        assert isinstance(result, str)

    def test_format(self):
        """Test that output format is correct."""
        result = get_utc_time()

        # Format: YYYY-MM-DDTHH:MM:SS.ffffffZ
        assert "T" in result
        assert result.endswith("Z")
        assert len(result) == 27  # 2024-01-15T10:30:00.000000Z


class TestConvertUtcLocalDatetime:
    """Tests for convert_utc_local_datetime function."""

    def test_convert_to_default_timezone(self):
        """Test conversion to default timezone (Asia/Shanghai)."""
        utc_dt = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        result = convert_utc_local_datetime(utc_dt)

        assert result.tzinfo is not None
        # Asia/Shanghai is UTC+8
        assert result.hour == 18

    def test_convert_to_specific_timezone(self):
        """Test conversion to specific timezone."""
        import pytz

        utc_dt = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        result = convert_utc_local_datetime(utc_dt, pytz.timezone("America/New_York"))

        assert result.tzinfo is not None


class TestGetStringTzTime:
    """Tests for get_string_tz_time function."""

    def test_default_timezone(self):
        """Test with default timezone (Asia/Singapore)."""
        result = get_string_tz_time()

        assert isinstance(result, str)
        # Format: YYYY-MM-DD HH:MM:SS.ffffff
        assert "-" in result
        assert ":" in result

    def test_custom_timezone(self):
        """Test with custom timezone."""
        result = get_string_tz_time(tz="UTC")

        assert isinstance(result, str)

    def test_custom_format(self):
        """Test with custom string format."""
        result = get_string_tz_time(string_format="%Y-%m-%d")

        assert isinstance(result, str)
        # Format: YYYY-MM-DD
        assert len(result) == 10


class TestTimestamp2Datetime:
    """Tests for timestamp2datetime function."""

    def test_convert_timestamp(self):
        """Test converting timestamp to datetime string."""
        timestamp = 1705315800.0  # 2024-01-15 10:30:00 UTC
        result = timestamp2datetime(timestamp)

        assert isinstance(result, str)
        assert "2024" in result

    def test_custom_format(self):
        """Test with custom format."""
        timestamp = 1705315800.0
        result = timestamp2datetime(timestamp, string_format="%Y-%m-%d")

        assert isinstance(result, str)


class TestDatetime2Timestamp:
    """Tests for datetime2timestamp function."""

    def test_convert_datetime_string(self):
        """Test converting datetime string to timestamp."""
        result = datetime2timestamp("2024-01-15 10:30:00.000")

        assert isinstance(result, float)
        assert result > 0

    def test_custom_format(self):
        """Test with custom format."""
        result = datetime2timestamp("2024-01-15", string_format="%Y-%m-%d")

        assert isinstance(result, float)


class TestStr2Datetime:
    """Tests for str2datetime function."""

    def test_convert_string(self):
        """Test converting string to datetime object."""
        result = str2datetime("2024-01-15 10:30:00.000")

        assert isinstance(result, datetime)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_custom_format(self):
        """Test with custom format."""
        result = str2datetime("2024-01-15", string_format="%Y-%m-%d")

        assert isinstance(result, datetime)
        assert result.hour == 0
        assert result.minute == 0


class TestDatetime2Str:
    """Tests for datetime2str function."""

    def test_convert_datetime(self):
        """Test converting datetime to string."""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = datetime2str(dt)

        assert isinstance(result, str)
        assert "2024" in result
        assert "01" in result
        assert "15" in result

    def test_custom_format(self):
        """Test with custom format."""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = datetime2str(dt, string_format="%Y-%m-%d")

        assert result == "2024-01-15"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
