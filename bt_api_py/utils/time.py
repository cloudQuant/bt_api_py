from __future__ import annotations

from datetime import datetime


def convert_utc_timestamp(timestamp):
    """Convert millisecond timestamp to datetime object

    Args:
        timestamp: Millisecond timestamp (int or string)

    Returns:
        datetime: UTC datetime object
    """
    if timestamp is None:
        return None

    # Convert to int if it's a string
    if isinstance(timestamp, str):
        try:
            timestamp = int(timestamp)
        except ValueError:
            return None

    # Convert milliseconds to seconds if needed
    if timestamp > 1000000000000:  # Millisecond timestamp
        timestamp = timestamp / 1000

    return datetime.utcfromtimestamp(timestamp).replace(microsecond=0)
