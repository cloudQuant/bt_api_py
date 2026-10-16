"""Retired legacy gateway client import path.

Importing this module is safe and does not load the old forwarding runtime.
Constructing its former client facade fails with an explicit migration error.
"""

from __future__ import annotations

from typing import Any

from bt_api_py._contracts.errors import LegacyForwardingRetiredError


class GatewayClient:
    """Fail-closed placeholder for the retired forwarding-backed client."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise LegacyForwardingRetiredError(
            "bt_api_py.gateway.client.GatewayClient",
            detail=(
                "use bt_api_py.service_runtime.ServiceClientRuntime with the typed "
                "ServiceMarketClient, ServiceAccountClient, or ServiceExecutionClient; "
                "Backtrader managed execution uses "
                "backtrader.stores.service_execution_session.ServiceExecutionSession(...) "
                "and then .run(cerebro). "
                "Keep a matching historical release when no service binding exists."
            ),
        )


__all__ = ["GatewayClient"]
