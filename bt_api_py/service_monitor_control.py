"""Retired Monitor-driven Risk control path (D63-35 option b, iteration 64).

Monitor stays an observer. Its durable control ledger no longer drives Risk
STOP_NEW/FREEZE effects through an in-process owner: ``MonitorRiskControlOwner``
and the runtime that maintained it have been removed. Emergency controls are
applied through the Gateway control port instead.

A deployment that still selects the Monitor ``control`` role is refused when
its components are validated, before any trusted resource provider runs, with
the finite code ``MONITOR_CONTROL_RETIRED``. It is never silently ignored.
"""

from __future__ import annotations

from typing import Any, NoReturn

from bt_api_monitor.service_control import (
    MONITOR_CONTROL_PUBLIC_PORT,
    MONITOR_CONTROL_SUBMIT_OPERATIONS,
    MonitorControlServiceError,
)

from .service_components import ServiceComponentError

MONITOR_CONTROL_RETIRED = "MONITOR_CONTROL_RETIRED"
MONITOR_CONTROL_OPERATIONS = MONITOR_CONTROL_SUBMIT_OPERATIONS


def _refuse_retired_control() -> NoReturn:
    raise ServiceComponentError(MONITOR_CONTROL_RETIRED)


class _RetiredOperations:
    """Refuse composition when the component catalog reads the role's operations."""

    def __get__(self, instance: object, owner: type | None = None) -> NoReturn:
        _refuse_retired_control()


class MonitorControlRuntime:
    """Composition guard for the retired Monitor ``control`` role.

    Component validation reads ``PUBLIC_OPERATIONS`` for the selected role and
    receives ``ServiceComponentError("MONITOR_CONTROL_RETIRED")``; direct
    construction is refused with the same code.
    """

    PUBLIC_PORT = MONITOR_CONTROL_PUBLIC_PORT
    PUBLIC_OPERATIONS = _RetiredOperations()

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        _refuse_retired_control()


__all__ = [
    "MONITOR_CONTROL_OPERATIONS",
    "MONITOR_CONTROL_RETIRED",
    "MonitorControlRuntime",
    "MonitorControlServiceError",
]
