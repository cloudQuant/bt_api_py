"""Shared utility functions for ticker data containers.

Reduces code duplication across 35+ exchange-specific ticker implementations
by providing common parse helpers for numeric API responses.
"""

from __future__ import annotations

from typing import Any


def parse_float(value: Any) -> float | None:
    """Parse value to float, returning None on failure.

    Handles None, string numeric values, and invalid types uniformly
    across all exchange ticker containers.

    Args:
        value: Value to parse (str, int, float, or None).

    Returns:
        Parsed float or None if value is None or cannot be parsed.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def parse_int(value: Any) -> int | None:
    """Parse value to int, returning None on failure.

    Handles None, string numeric values, and invalid types uniformly
    across all exchange ticker containers.

    Args:
        value: Value to parse (str, int, float, or None).

    Returns:
        Parsed int or None if value is None or cannot be parsed.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
