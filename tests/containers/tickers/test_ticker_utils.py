"""Tests for ticker_utils module."""

from __future__ import annotations

from bt_api_py.containers.tickers.ticker_utils import parse_float, parse_int


class TestParseFloat:
    """Tests for parse_float function."""

    def test_parse_float_from_string(self):
        """Test parsing float from string."""
        assert parse_float("50000.5") == 50000.5

    def test_parse_float_from_int(self):
        """Test parsing float from int."""
        assert parse_float(50000) == 50000.0

    def test_parse_float_from_float(self):
        """Test parsing float from float."""
        assert parse_float(50000.5) == 50000.5

    def test_parse_float_from_none(self):
        """Test parsing float from None returns None."""
        assert parse_float(None) is None

    def test_parse_float_from_invalid_string(self):
        """Test parsing float from invalid string returns None."""
        assert parse_float("invalid") is None


class TestParseInt:
    """Tests for parse_int function."""

    def test_parse_int_from_string(self):
        """Test parsing int from string."""
        assert parse_int("12345") == 12345

    def test_parse_int_from_int(self):
        """Test parsing int from int."""
        assert parse_int(12345) == 12345

    def test_parse_int_from_float(self):
        """Test parsing int from float."""
        assert parse_int(12345.6) == 12345

    def test_parse_int_from_none(self):
        """Test parsing int from None returns None."""
        assert parse_int(None) is None

    def test_parse_int_from_invalid_string(self):
        """Test parsing int from invalid string returns None."""
        assert parse_int("invalid") is None
