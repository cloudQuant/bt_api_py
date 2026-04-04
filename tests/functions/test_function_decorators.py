"""Tests for functions/decorators.py."""

from bt_api_py.functions.decorators import deprecated, time_this_function


class TestDecorators:
    """Tests for decorators."""

    def test_deprecated_exists(self):
        """Test deprecated decorator is defined."""
        assert deprecated is not None

    def test_time_this_function_exists(self):
        """Test time_this_function decorator is defined."""
        assert time_this_function is not None
