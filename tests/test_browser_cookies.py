"""Tests for browser_cookies module."""

import pytest

from bt_api_py.functions.browser_cookies import extract_cookie_string


class TestExtractCookieString:
    """Tests for extract_cookie_string function."""

    def test_empty_string(self):
        """Test empty string returns empty dict."""
        result = extract_cookie_string("")

        assert result == {}

    def test_single_cookie(self):
        """Test single cookie."""
        result = extract_cookie_string("key1=value1")

        assert result == {"key1": "value1"}

    def test_multiple_cookies(self):
        """Test multiple cookies."""
        result = extract_cookie_string("key1=value1; key2=value2; key3=value3")

        assert result == {"key1": "value1", "key2": "value2", "key3": "value3"}

    def test_cookie_with_spaces(self):
        """Test cookies with spaces."""
        result = extract_cookie_string("  key1 = value1  ;  key2 = value2  ")

        assert result == {"key1": "value1", "key2": "value2"}

    def test_cookie_with_equals_in_value(self):
        """Test cookie with equals sign in value."""
        result = extract_cookie_string("key1=value1=extra")

        assert result == {"key1": "value1=extra"}

    def test_invalid_cookie_no_equals(self):
        """Test invalid cookie without equals."""
        result = extract_cookie_string("invalid_cookie")

        assert result == {}

    def test_mixed_valid_invalid(self):
        """Test mix of valid and invalid cookies."""
        result = extract_cookie_string("key1=value1; invalid; key2=value2")

        assert result == {"key1": "value1", "key2": "value2"}

    def test_none_input(self):
        """Test None input."""
        result = extract_cookie_string(None)

        assert result == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
