"""
Shared normalize functions for OKX API responses.
These are used across multiple mixin modules.
"""

from typing import Any


def generic_normalize_function(input_data: Any, extra_data: Any) -> None:
    """Generic normalize function for OKX API responses.
    Extracts 'data' list and checks 'code' for status."""
    status = input_data.get("code") == "0"
    if "data" not in input_data:
        return [], status
    data = input_data["data"]
    if isinstance(data, list):
        return data, status
    return [data] if data else [], status
