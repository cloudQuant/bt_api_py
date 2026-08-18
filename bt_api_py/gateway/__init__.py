"""Gateway client compatibility exports and safe-by-default configuration."""

from __future__ import annotations

from bt_api_py.gateway.client import GatewayClient
from bt_api_py.gateway.config import GatewayConfig
from bt_api_py.gateway.safety import GatewaySafetyError, is_loopback_or_ipc

__all__ = [
    "GatewayClient",
    "GatewayConfig",
    "GatewaySafetyError",
    "is_loopback_or_ipc",
]
