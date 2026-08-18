"""Compatibility alias for the legacy ``BtApiForwardingAdapter``.

The corrected bridge lives in :mod:`bt_api_py.forwarding.btapi_bridge`. This
module keeps the old import path stable while delegating to the fixed
implementation (U-02: topic strings are converted to ``[{"topic": ...}]``).
"""

from __future__ import annotations

from bt_api_py.forwarding.btapi_bridge import BtApiForwardingBridge

BtApiForwardingAdapter = BtApiForwardingBridge

__all__ = ["BtApiForwardingAdapter"]
