"""
SatoshiTango Feed Module
"""

from __future__ import annotations

from bt_api_py.feeds.live_satoshitango.request_base import SatoshiTangoRequestData
from bt_api_py.feeds.live_satoshitango.spot import SatoshiTangoRequestDataSpot

__all__ = [
    "SatoshiTangoRequestData",
    "SatoshiTangoRequestDataSpot",
]
