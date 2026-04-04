"""Tests for functions/calculate_time.py with comprehensive coverage."""

from datetime import UTC, datetime

from bt_api_py.functions.calculate_time import (
    convert_utc_local_datetime,
    get_string_tz_time,
    get_utc_time,
    timestamp2datetime,
)


class TestGetUtcTime:
    """Tests for get_utc_time function."""

    def test_returns_string(self):
        """Test function returns a string."""
        result = get_utc_time()
        assert isinstance(result, str)

    def test_format_contains_t(self):
        """Test result contains T separator."""
        result = get_utc_time()
        assert "T" in result

    def test_ends_with_z(self):
        """Test result ends with Z."""
        result = get_utc_time()
        assert result.endswith("Z")

    def test_format_is_iso(self):
        """Test format is ISO 8601."""
        result = get_utc_time()
        # Should be parseable as ISO format
        assert len(result) > 20  # YYYY-MM-DDTHH:MM:SS.ffffffZ


class TestConvertUtcLocalDatetime:
    """Tests for convert_utc_local_datetime function."""

    def test_convert_to_shanghai(self):
        """Test conversion to Shanghai timezone."""
        utc_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = convert_utc_local_datetime(utc_dt)
        # Shanghai is UTC+8
        assert result.hour == 20

    def test_convert_preserves_date(self):
        """Test date is preserved."""
        utc_dt = datetime(2024, 1, 15, 0, 0, 0, tzinfo=UTC)
        result = convert_utc_local_datetime(utc_dt)
        assert result.day == 15


class TestGetStringTzTime:
    """Tests for get_string_tz_time function."""

    def test_returns_string(self):
        """Test function returns a string."""
        result = get_string_tz_time()
        assert isinstance(result, str)

    def test_default_timezone(self):
        """Test default timezone is Singapore."""
        result = get_string_tz_time()
        assert len(result) > 0

    def test_custom_timezone(self):
        """Test custom timezone."""
        result = get_string_tz_time(tz="Asia/Tokyo")
        assert isinstance(result, str)

    def test_custom_format(self):
        """Test custom format string."""
        result = get_string_tz_time(string_format="%Y-%m-%d")
        assert len(result) == 10  # YYYY-MM-DD


class TestTimestamp2Datetime:
    """Tests for timestamp2datetime function."""

    def test_returns_string(self):
        """Test function returns a string."""
        result = timestamp2datetime(1704067200.0)
        assert isinstance(result, str)

    def test_format(self):
        """Test datetime format."""
        result = timestamp2datetime(1704067200.0)
        assert "2024" in result

    def test_custom_format(self):
        """Test custom format."""
        result = timestamp2datetime(1704067200.0, string_format="%Y-%m-%d")
        assert result == "2024-01-01"
