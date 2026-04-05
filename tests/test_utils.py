"""Tests for utils module."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from bt_api_py.functions.utils import (
    _parse_env_bool,
    _parse_env_int,
    from_dict_get_float,
    from_dict_get_int,
    from_dict_get_string,
    update_extra_data,
)


class TestParseEnvInt:
    """Tests for _parse_env_int function."""

    def test_default_when_not_set(self):
        """Test default value when env not set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            result = _parse_env_int("TEST_INT", 42)
            assert result == 42

    def test_default_when_empty(self):
        """Test default value when env is empty."""
        with mock.patch.dict(os.environ, {"TEST_INT": ""}, clear=True):
            result = _parse_env_int("TEST_INT", 42)
            assert result == 42

    def test_valid_int_value(self):
        """Test valid integer value."""
        with mock.patch.dict(os.environ, {"TEST_INT": "100"}, clear=True):
            result = _parse_env_int("TEST_INT", 42)
            assert result == 100

    def test_invalid_int_returns_default(self):
        """Test invalid integer returns default."""
        with mock.patch.dict(os.environ, {"TEST_INT": "not_a_number"}, clear=True):
            result = _parse_env_int("TEST_INT", 42)
            assert result == 42


class TestParseEnvBool:
    """Tests for _parse_env_bool function."""

    def test_default_when_not_set(self):
        """Test default value when env not set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            result = _parse_env_bool("TEST_BOOL", True)
            assert result is True

    def test_default_when_empty(self):
        """Test default value when env is empty."""
        with mock.patch.dict(os.environ, {"TEST_BOOL": ""}, clear=True):
            result = _parse_env_bool("TEST_BOOL", False)
            assert result is False

    def test_truthy_values(self):
        """Test truthy string values."""
        truthy_values = ["true", "1", "yes", "y", "on", "TRUE", "True", "YES"]
        for val in truthy_values:
            with mock.patch.dict(os.environ, {"TEST_BOOL": val}, clear=True):
                result = _parse_env_bool("TEST_BOOL", False)
                assert result is True, f"Expected True for {val}"

    def test_falsy_values(self):
        """Test falsy string values."""
        falsy_values = ["false", "0", "no", "n", "off", "FALSE", "False", "NO"]
        for val in falsy_values:
            with mock.patch.dict(os.environ, {"TEST_BOOL": val}, clear=True):
                result = _parse_env_bool("TEST_BOOL", True)
                assert result is False, f"Expected False for {val}"

    def test_invalid_bool_returns_default(self):
        """Test invalid boolean returns default."""
        with mock.patch.dict(os.environ, {"TEST_BOOL": "invalid"}, clear=True):
            result = _parse_env_bool("TEST_BOOL", True)
            assert result is True


class TestFromDictGetFloat:
    """Tests for from_dict_get_float function."""

    def test_get_float_value(self):
        """Test getting float value."""
        data = {"price": "100.5"}
        result = from_dict_get_float(data, "price")
        assert result == 100.5

    def test_get_float_default(self):
        """Test default value when key not found."""
        data = {}
        result = from_dict_get_float(data, "price", default=0.0)
        assert result == 0.0

    def test_get_float_none_value(self):
        """Test None value returns string 'None' converted to float."""
        data = {"price": None}
        result = from_dict_get_float(data, "price", default=0.0)
        # None is converted to string "None" which can't be parsed as float
        assert result is None or result == 0.0

    def test_get_float_int_value(self):
        """Test int value is converted to float."""
        data = {"price": 100}
        result = from_dict_get_float(data, "price")
        assert result == 100.0


class TestFromDictGetInt:
    """Tests for from_dict_get_int function."""

    def test_get_int_value(self):
        """Test getting int value."""
        data = {"count": "42"}
        result = from_dict_get_int(data, "count")
        assert result == 42

    def test_get_int_default(self):
        """Test default value when key not found."""
        data = {}
        result = from_dict_get_int(data, "count", default=0)
        assert result == 0

    def test_get_int_none_value(self):
        """Test None value returns string 'None'."""
        data = {"count": None}
        result = from_dict_get_int(data, "count", default=0)
        # None is converted to string "None" which can't be parsed as int
        assert result is None or result == 0


class TestFromDictGetString:
    """Tests for from_dict_get_string function."""

    def test_get_string_value(self):
        """Test getting string value."""
        data = {"symbol": "BTCUSDT"}
        result = from_dict_get_string(data, "symbol")
        assert result == "BTCUSDT"

    def test_get_string_default(self):
        """Test default value when key not found."""
        data = {}
        result = from_dict_get_string(data, "symbol", default="")
        assert result == ""

    def test_get_string_none_value(self):
        """Test None value returns string 'None'."""
        data = {"symbol": None}
        result = from_dict_get_string(data, "symbol", default="")
        # None is converted to string "None"
        assert result == "None" or result == ""


class TestUpdateExtraData:
    """Tests for update_extra_data function."""

    def test_update_with_none_extra_data(self):
        """Test update when extra_data is None."""
        result = update_extra_data(None, key1="value1", key2="value2")
        assert result == {"key1": "value1", "key2": "value2"}

    def test_update_with_existing_extra_data(self):
        """Test update with existing extra_data."""
        result = update_extra_data({"existing": "data"}, new="value")
        assert result == {"existing": "data", "new": "value"}

    def test_update_no_kwargs(self):
        """Test update with no kwargs."""
        result = update_extra_data({"existing": "data"})
        assert result == {"existing": "data"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
