"""SDK-owned write admission (iteration 62, D62-01/02, INV62-01/02).

Writes are disabled by default.  A host enables them explicitly per route
(``BtApi(write_admission={"CTP___FUTURE": True})``).  The decision is
environment-neutral: simulation and production routes use the same rule, and
mode names or front registries never grant write permission.

Every account-mutating native call needs a one-shot :class:`WriteGrant` issued
here; providers consume it through :meth:`WriteAdmission.consume` immediately
before the native call.  A grant is bound to the route, the operation and the
provider session generation, expires quickly and can be consumed only once.
"""

from __future__ import annotations

import threading
import time
from typing import Mapping, Optional
from uuid import uuid4

from bt_api_base.direct_write_protocol import (
    WriteAdmissionDenied,
    WriteGrant,
    WriteGrantVerifier,
    WriteOperation,
)

__all__ = ("WriteAdmission", "WriteAdmissionDenied")

_GRANT_TTL_NS = 30_000_000_000


class WriteAdmission(WriteGrantVerifier):
    """Per-``BtApi`` write admission with one-shot grants."""

    def __init__(self, enabled_routes: Optional[Mapping[str, bool]] = None) -> None:
        self._lock = threading.Lock()
        self._enabled: dict[str, bool] = {}
        self._outstanding: dict[str, WriteGrant] = {}
        for route, enabled in dict(enabled_routes or {}).items():
            if type(route) is not str or not route:
                raise ValueError("write_admission routes must be non-empty strings")
            if type(enabled) is not bool:
                raise ValueError("write_admission values must be booleans")
            self._enabled[route] = enabled

    def is_enabled(self, exchange_name: str) -> bool:
        with self._lock:
            return self._enabled.get(exchange_name) is True

    def enabled_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(route for route, on in self._enabled.items() if on))

    def issue(
        self,
        exchange_name: str,
        operation: WriteOperation,
        session_generation: Optional[int],
    ) -> WriteGrant:
        """Issue one grant or raise ``write_admission_disabled``."""

        if type(operation) is not WriteOperation:
            raise WriteAdmissionDenied("write_operation_invalid")
        with self._lock:
            if self._enabled.get(exchange_name) is not True:
                raise WriteAdmissionDenied("write_admission_disabled", exchange_name)
            grant = WriteGrant(
                exchange_name=exchange_name,
                operation=operation,
                session_generation=session_generation,
                grant_id=uuid4().hex,
                issued_monotonic_ns=time.monotonic_ns(),
            )
            self._outstanding[grant.grant_id] = grant
            return grant

    def consume(
        self,
        grant: WriteGrant,
        *,
        exchange_name: str,
        operation: WriteOperation,
        session_generation: Optional[int],
    ) -> None:
        """Consume ``grant`` exactly once for exactly this native call."""

        if type(grant) is not WriteGrant:
            raise WriteAdmissionDenied("write_grant_invalid")
        with self._lock:
            stored = self._outstanding.pop(grant.grant_id, None)
            enabled = self._enabled.get(exchange_name) is True
        if stored is None or stored is not grant:
            raise WriteAdmissionDenied("write_grant_invalid_or_consumed")
        if not enabled:
            raise WriteAdmissionDenied("write_admission_disabled", exchange_name)
        if grant.exchange_name != exchange_name or grant.operation is not operation:
            raise WriteAdmissionDenied("write_grant_scope_mismatch")
        if grant.session_generation != session_generation:
            raise WriteAdmissionDenied("write_grant_session_changed")
        if time.monotonic_ns() - grant.issued_monotonic_ns > _GRANT_TTL_NS:
            raise WriteAdmissionDenied("write_grant_expired")

    def revoke(self, exchange_name: Optional[str] = None) -> None:
        """Drop outstanding grants (all routes or one route)."""

        with self._lock:
            if exchange_name is None:
                self._outstanding.clear()
                return
            for grant_id, grant in list(self._outstanding.items()):
                if grant.exchange_name == exchange_name:
                    del self._outstanding[grant_id]
