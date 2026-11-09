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
from contextlib import contextmanager
from typing import Mapping, Optional
from uuid import uuid4

from bt_api_base.direct_write_protocol import (
    SettlementWriteBinding,
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
        self._settlement_claim_coordinator = None
        self._ever_issued = False
        for route, enabled in dict(enabled_routes or {}).items():
            if type(route) is not str or not route:
                raise ValueError("write_admission routes must be non-empty strings")
            if type(enabled) is not bool:
                raise ValueError("write_admission values must be booleans")
            self._enabled[route] = enabled

    def _bind_settlement_claim_coordinator(self, coordinator) -> None:
        """Bind one experimental owner before use; this does not grant authority.

        Every grant mutation invalidates the owner's write-admission domain and
        publishes UNAVAILABLE. The owner must independently prove all sources
        before any future production enabler could publish a usable state.
        Binding is per instance, cannot be replaced, and is not wired by direct
        or Gateway service construction. Lock order is admission then claim;
        the claim coordinator does not call back into this object.
        """
        from bt_api_py.gateway_settlement_claim import GatewaySettlementClaimCoordinator

        if type(coordinator) is not GatewaySettlementClaimCoordinator:
            raise TypeError("invalid settlement claim coordinator")
        with self._lock:
            if self._settlement_claim_coordinator is not None or self._ever_issued:
                raise RuntimeError("settlement claim coordinator cannot be bound")
            self._settlement_claim_coordinator = coordinator
            with self._settlement_claim_change_locked():
                pass

    @contextmanager
    def _settlement_claim_change_locked(self):
        coordinator = self._settlement_claim_coordinator
        if coordinator is None:
            yield
            return
        try:
            token = coordinator.begin_change("write_admission")
        except BaseException:
            # Even if publication cannot begin, the coordinator cannot retain
            # any previously valid candidates.
            coordinator.close()
            raise
        try:
            yield
            coordinator.finish_change(token, "UNAVAILABLE")
        except BaseException:
            try:
                coordinator.poison(token)
            except BaseException:
                coordinator.close()
            raise

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
        *,
        settlement_binding: Optional[SettlementWriteBinding] = None,
    ) -> WriteGrant:
        """Issue one grant or raise ``write_admission_disabled``."""

        if type(operation) is not WriteOperation:
            raise WriteAdmissionDenied("write_operation_invalid")
        if operation is WriteOperation.SETTLEMENT_CONFIRM:
            if (type(settlement_binding) is not SettlementWriteBinding
                    or settlement_binding.observation.exchange_name != exchange_name
                    or settlement_binding.observation.session_generation != session_generation):
                raise WriteAdmissionDenied("settlement_binding_invalid")
        elif settlement_binding is not None:
            raise WriteAdmissionDenied("settlement_binding_invalid")
        with self._lock:
            if self._enabled.get(exchange_name) is not True:
                raise WriteAdmissionDenied("write_admission_disabled", exchange_name)
            grant = WriteGrant(
                exchange_name=exchange_name,
                operation=operation,
                session_generation=session_generation,
                grant_id=uuid4().hex,
                issued_monotonic_ns=time.monotonic_ns(),
                settlement_binding=settlement_binding,
            )
            try:
                with self._settlement_claim_change_locked():
                    self._outstanding[grant.grant_id] = grant
            except BaseException:
                self._outstanding.pop(grant.grant_id, None)
                raise
            self._ever_issued = True
            return grant

    def consume(
        self,
        grant: WriteGrant,
        *,
        exchange_name: str,
        operation: WriteOperation,
        session_generation: Optional[int],
        settlement_binding: Optional[SettlementWriteBinding] = None,
    ) -> None:
        """Consume ``grant`` exactly once for exactly this native call."""

        if type(grant) is not WriteGrant:
            raise WriteAdmissionDenied("write_grant_invalid")
        with self._lock:
            with self._settlement_claim_change_locked():
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
        if (operation is WriteOperation.SETTLEMENT_CONFIRM and (
                type(settlement_binding) is not SettlementWriteBinding
                or grant.settlement_binding is not settlement_binding
                or settlement_binding.observation.exchange_name != exchange_name
                or settlement_binding.observation.session_generation != session_generation)):
            raise WriteAdmissionDenied("settlement_binding_invalid")
        if operation is not WriteOperation.SETTLEMENT_CONFIRM and (
                settlement_binding is not None or grant.settlement_binding is not None):
            raise WriteAdmissionDenied("settlement_binding_invalid")
        if time.monotonic_ns() - grant.issued_monotonic_ns > _GRANT_TTL_NS:
            raise WriteAdmissionDenied("write_grant_expired")

    def revoke(self, exchange_name: Optional[str] = None) -> None:
        """Drop outstanding grants (all routes or one route)."""

        with self._lock:
            def clear() -> None:
                if exchange_name is None:
                    self._outstanding.clear()
                else:
                    for grant_id, grant in list(self._outstanding.items()):
                        if grant.exchange_name == exchange_name:
                            del self._outstanding[grant_id]

            try:
                with self._settlement_claim_change_locked():
                    clear()
            except BaseException:
                # Revocation must still take effect when the fence fails.
                clear()
                raise

    def revoke_grant(self, grant: WriteGrant) -> None:
        """Forget one exact unconsumed grant without affecting other writes."""
        if type(grant) is not WriteGrant:
            return
        with self._lock:
            def clear() -> None:
                if self._outstanding.get(grant.grant_id) is grant:
                    del self._outstanding[grant.grant_id]

            try:
                with self._settlement_claim_change_locked():
                    clear()
            except BaseException:
                clear()
                raise
