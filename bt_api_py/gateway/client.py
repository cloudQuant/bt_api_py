"""Compatibility GatewayClient used by backtrader live stores.

The standalone gateway runtime exposes ZeroMQ command, market and private event
endpoints.  This adapter keeps the public import path stable while reusing the
forwarding client protocol underneath.
"""

from __future__ import annotations

import warnings
from typing import Any, cast

from bt_api_py.forwarding.client import ZmqForwardingClient


class GatewayClient(ZmqForwardingClient):
    """Client facade for an already-running bt_api_py gateway runtime."""

    def __init__(
        self,
        *,
        gateway_command_endpoint: str = "",
        gateway_event_endpoint: str = "",
        gateway_market_endpoint: str = "",
        command_endpoint: str = "",
        event_endpoint: str = "",
        market_endpoint: str = "",
        private_endpoint: str = "",
        exchange_type: str = "CTP",
        asset_type: str = "FUTURE",
        account_id: str = "paper",
        strategy_id: str = "default",
        gateway_command_timeout_sec: float | int | str | None = None,
        command_timeout_sec: float | int | str | None = None,
        command_timeout_ms: int | None = None,
        **kwargs: Any,
    ) -> None:
        if (
            gateway_command_endpoint
            or gateway_event_endpoint
            or gateway_market_endpoint
            or event_endpoint
        ):
            warnings.warn(
                "gateway_*_endpoint and event_endpoint are deprecated aliases; "
                "use command_endpoint/market_endpoint/private_endpoint",
                DeprecationWarning,
                stacklevel=2,
            )
        market = (
            gateway_market_endpoint or market_endpoint or event_endpoint or gateway_event_endpoint
        )
        command = gateway_command_endpoint or command_endpoint
        private = private_endpoint or gateway_event_endpoint or event_endpoint or market
        if not market or not command:
            raise ValueError("GatewayClient requires command and market/event endpoints")

        timeout_ms = command_timeout_ms
        if timeout_ms is None:
            timeout_sec = (
                gateway_command_timeout_sec
                if gateway_command_timeout_sec not in (None, "")
                else command_timeout_sec
            )
            resolved = cast(
                "float | int | str",
                timeout_sec if timeout_sec not in (None, "") else 2.0,
            )
            timeout_ms = int(float(resolved) * 1000)

        super().__init__(
            market_endpoint=str(market),
            command_endpoint=str(command),
            private_endpoint=str(private) if private else None,
            exchange=str(exchange_type or kwargs.get("exchange") or "CTP"),
            market_type=str(asset_type or kwargs.get("market_type") or "FUTURE"),
            account_id=str(
                account_id or kwargs.get("investor_id") or kwargs.get("user_id") or "paper"
            ),
            strategy_id=str(strategy_id or "default"),
            command_timeout_ms=timeout_ms,
            event_cache_size=kwargs.get("event_cache_size", 4096),
        )
