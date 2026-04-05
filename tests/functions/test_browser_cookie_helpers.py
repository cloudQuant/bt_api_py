"""Focused tests for browser cookie helper utilities."""

from __future__ import annotations

from bt_api_py.functions.browser_cookies import (
    _format_cookie_preview,
    cookies_to_header,
    extract_cookie_string,
)


def test_extract_cookie_string_parses_pairs() -> None:
    cookies = extract_cookie_string("a=1; b=two")

    assert cookies == {"a": "1", "b": "two"}


def test_cookies_to_header_joins_pairs() -> None:
    header = cookies_to_header({"a": "1", "b": "two"})

    assert header == "a=1; b=two"


def test_format_cookie_preview_masks_long_values() -> None:
    preview = _format_cookie_preview({"token": "abcdefghijklmnopqrstuvwxyz"})

    assert preview == ["  token: abcdefghijklmnopqrst..."]
