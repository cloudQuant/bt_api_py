"""Retired SDK forwarding runtime import paths.

Managed writes now use ``ServiceExecutionSession`` over the ServiceHost
``service.order_entry`` port.  This module intentionally has no imports of a
provider adapter, GatewayConfig, or ZMQ transport so importing the old paths
cannot assemble or start the legacy runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NoReturn

from bt_api_py._contracts.errors import LegacyForwardingRetiredError


_MIGRATION_DETAIL = (
    "the legacy forwarding runtime was retired; use BtApi with the direct "
    "transport for ordinary backtest calls, or Backtrader ServiceExecutionSession "
    "with ServiceHost and service.order_entry for managed writes"
)


def _raise_retired(target: str) -> NoReturn:
    raise LegacyForwardingRetiredError(target, detail=_MIGRATION_DETAIL)


@dataclass
class ForwardingRuntime:
    """Retired embedded forwarding writer; use ServiceHost instead."""

    adapter: Any
    bus: Any | None = None
    risk_rules: Any | None = None
    state_store: Any | None = None
    source: Any | None = None
    write_enabled: bool = False
    write_scope: tuple[str, str, str] | None = None

    def __post_init__(self) -> None:
        _raise_retired("bt_api_py.forwarding.service.ForwardingRuntime")

    async def start(self) -> bool:
        _raise_retired("bt_api_py.forwarding.service.ForwardingRuntime.start")

    async def stop(self) -> bool:
        _raise_retired("bt_api_py.forwarding.service.ForwardingRuntime.stop")

    async def health(self) -> dict[str, object]:
        _raise_retired("bt_api_py.forwarding.service.ForwardingRuntime.health")


class ZmqForwardingRuntime:
    """Retired standalone ZMQ writer; use ServiceHost and service.order_entry."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        _raise_retired("bt_api_py.forwarding.service.ZmqForwardingRuntime")

    @property
    def is_running(self) -> bool:
        return False

    def start_sync(self) -> None:
        _raise_retired("bt_api_py.forwarding.service.ZmqForwardingRuntime.start_sync")

    def stop_sync(self) -> None:
        _raise_retired("bt_api_py.forwarding.service.ZmqForwardingRuntime.stop_sync")

    async def health(self) -> dict[str, object]:
        _raise_retired("bt_api_py.forwarding.service.ZmqForwardingRuntime.health")


__all__ = ["ForwardingRuntime", "ZmqForwardingRuntime"]
