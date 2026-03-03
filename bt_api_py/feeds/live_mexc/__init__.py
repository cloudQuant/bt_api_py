"""
MEXC exchange feed implementation

Provides REST API and WebSocket feeds for MEXC exchange spot trading.
"""

from .request_base import MexcRequestData
from .spot import MexcRequestDataSpot

__all__ = ['MexcRequestData', 'MexcRequestDataSpot']