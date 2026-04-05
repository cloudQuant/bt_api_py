"""
BTCTurk Feed Module
"""

from __future__ import annotations

from bt_api_py.feeds.live_btcturk.request_base import BTCTurkRequestData
from bt_api_py.feeds.live_btcturk.spot import BTCTurkRequestDataSpot

__all__ = ["BTCTurkRequestData", "BTCTurkRequestDataSpot"]
