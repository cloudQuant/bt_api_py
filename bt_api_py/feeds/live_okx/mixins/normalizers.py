"""
Shared normalize functions for OKX API responses.
These are used across multiple mixin modules.
"""


def generic_normalize_function(input_data, extra_data):
    """Generic normalize function for OKX API responses.
    Extracts 'data' list and checks 'code' for status."""
    status = True if input_data.get("code") == "0" else False
    if "data" not in input_data:
        return [], status
    data = input_data["data"]
    if isinstance(data, list):
        return data, status
    return [data] if data else [], status
