"""Focused tests for functions.utils helper behavior."""

from __future__ import annotations

from bt_api_py.functions.utils import (
    from_dict_get_bool,
    from_dict_get_float,
    from_dict_get_int,
    from_dict_get_string,
    get_package_path,
    update_extra_data,
)


def test_get_package_path_missing_package_returns_none() -> None:
    assert get_package_path("definitely_missing_package_name") is None


def test_update_extra_data_returns_new_dict_when_none() -> None:
    result = update_extra_data(None, foo="bar")

    assert result == {"foo": "bar"}


def test_from_dict_helpers_handle_common_values() -> None:
    content = {"s": 123, "b": "true", "f": "1.25", "i": "9"}

    assert from_dict_get_string(content, "s") == "123"
    assert from_dict_get_bool(content, "b") is True
    assert from_dict_get_float(content, "f") == 1.25
    assert from_dict_get_int(content, "i") == 9
