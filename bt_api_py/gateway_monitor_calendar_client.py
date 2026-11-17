"""Authenticated Monitor access to a simulated Gateway calendar range.

The already-started runtime remains owned, maintained and closed by the caller.
Domain and account are from its authenticated registration, not a wire echo.
This observation neither establishes exchange calendar truth nor grants trading.
"""

from __future__ import annotations

import math
import os
import re
import threading
from dataclasses import dataclass, fields

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_calendar_codec import (
    decode_calendar_windows_query,
    validate_calendar_windows_response,
)
from bt_api_py.gateway_strategy_calendar import GatewayCalendarWindowsResult
from bt_api_py.service_runtime import (
    RegisteredBinding,
    ServiceClientRuntime,
    _bound_binding_values,
    _bound_runtime_values,
)
from bt_api_py.service_runtime_thread import _copy_wire

OPERATION = "calendar.monitor.windows.v1"
OPERATIONS = frozenset({OPERATION})


class MonitorCalendarClientError(RuntimeError):
    """Finite local error without transport details or source paths."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class MonitorCalendarWindowsResult:
    """Immutable simulated range plus the local authenticated account scope."""

    schema: str
    scope: object
    registration_id: str
    service_epoch: str
    status: str
    reason: str
    binding: object
    policy: object
    gaps: tuple
    windows: tuple
    source_authentication: str
    trade_authorization: str
    execution_domain_ref: str
    account_ref: str


class GatewayMonitorCalendarClient:
    """Read one fixed authenticated registration from its original owner thread."""

    def __init__(self, runtime, *, identity, scope, peer, service_id, instance_id, scope_ref):
        if (
            type(runtime) is not ServiceClientRuntime
            or type(identity) is not WireIdentity
            or identity.role is not WireRole.SERVICE
            or identity.delegate_ref is not None
            or type(peer) is not WireIdentity
            or peer.role is not WireRole.SERVICE
            or peer.delegate_ref is not None
            or type(scope) is not WireScope
            or scope.account_ref is None
            or scope != WireScope(scope.execution_domain_ref, account_ref=scope.account_ref)
            or any(
                type(value) is not str or not value
                for value in (service_id, instance_id, scope_ref)
            )
        ):
            raise MonitorCalendarClientError("INVALID_CONFIGURATION")
        self._runtime = self._original_runtime = runtime
        self._identity, self._scope, self._peer = identity, scope, peer
        self._service_id, self._instance_id, self._scope_ref = service_id, instance_id, scope_ref
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._closed = False
        try:
            _, self._signature, self._lineage, _ = self._observe()
        except Exception:
            raise MonitorCalendarClientError("BINDING_UNAVAILABLE") from None

    def _observe(self):
        if (
            self._closed
            or os.getpid() != self._pid
            or threading.current_thread() is not self._thread
            or self._runtime is not self._original_runtime
        ):
            raise ValueError
        runtime = self._runtime
        if (
            runtime.identity != self._identity
            or runtime.state != "registered"
            or runtime.healthy is not True
        ):
            raise ValueError
        matches = [
            b
            for b in runtime.bindings
            if type(b) is RegisteredBinding
            and b.active
            and b.mount == "upstream.calendar"
            and b.scope_ref == self._scope_ref
        ]
        if len(matches) != 1:
            raise ValueError
        binding = matches[0]
        if (
            binding.service_id != self._service_id
            or binding.instance_id != self._instance_id
            or binding.scope != self._scope
            or binding.operations != OPERATIONS
            or binding.remote_lease_inactive
            or not runtime._has_current_registration_observation(binding)
        ):
            raise ValueError
        now = runtime._monotonic_now()
        if (
            type(now) not in (int, float)
            or not math.isfinite(now)
            or type(binding.lease_deadline) not in (int, float)
            or not math.isfinite(binding.lease_deadline)
            or now >= binding.lease_deadline
        ):
            raise ValueError
        proof = runtime.authenticated_peer_for_binding(binding)
        if (
            type(proof) is not tuple
            or len(proof) != 2
            or proof[0] != self._peer
            or type(proof[1]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", proof[1]) is None
        ):
            raise ValueError
        registration = runtime._registration_lineage.get(id(binding))
        if type(registration) is not tuple or len(registration) not in (9, 10):
            raise ValueError
        signature = (
            tuple(
                (name, value)
                for name, value in _bound_binding_values(binding)
                if name not in {"expires_at", "lease_deadline", "renewal_due_at"}
            ),
            _bound_runtime_values(runtime),
            proof,
        )
        transport = runtime._transports.get((binding.service_id, binding.transport_ref))
        if transport is None:
            raise ValueError
        return binding, signature, registration[2:9], transport

    def _current(self):
        binding, signature, lineage, transport = self._observe()
        if (
            signature != self._signature
            or len(lineage) != len(self._lineage)
            or any(a is not b for a, b in zip(lineage, self._lineage, strict=True))
        ):
            raise ValueError
        return binding, transport

    def windows(self, *, route, venue, kind, product, contract, start_utc, end_utc):
        try:
            scope, _, _ = decode_calendar_windows_query(
                {
                    "mode": "SIMULATION_ONLY",
                    "route": route,
                    "venue": venue,
                    "kind": kind,
                    "product": product,
                    "contract": contract,
                    "start_utc": start_utc,
                    "end_utc": end_utc,
                }
            )
        except Exception:
            raise MonitorCalendarClientError("INVALID_REQUEST") from None
        try:
            binding, transport = self._current()
            response = self._runtime.call(
                "upstream.calendar",
                OPERATION,
                scope,
                account_ref=self._scope.account_ref,
                scope_ref=self._scope_ref,
                max_reply_message_bytes=65536,
                _expected_binding=binding,
                _expected_binding_values=_bound_binding_values(binding),
                _expected_runtime_values=_bound_runtime_values(self._runtime),
                _expected_transport=transport,
                _expected_peer=self._peer,
                _expected_fingerprint=self._signature[2][1],
            )
            current, _ = self._current()
            if current is not binding:
                raise ValueError
            value = validate_calendar_windows_response(_copy_wire(response))
            if (
                value["scope"] != scope
                or value["registration_id"] != binding.registration_id
                or value["service_epoch"] != binding.service_epoch
                or current.registration_id != binding.registration_id
                or current.service_epoch != binding.service_epoch
            ):
                raise ValueError
            detached = GatewayCalendarWindowsResult.from_wire(value)
            result = MonitorCalendarWindowsResult(
                **{
                    field.name: getattr(detached, field.name)
                    for field in fields(GatewayCalendarWindowsResult)
                },
                execution_domain_ref=self._scope.execution_domain_ref,
                account_ref=self._scope.account_ref,
            )
            current, _ = self._current()
            if current is not binding:
                raise ValueError
            return result
        except Exception:
            raise MonitorCalendarClientError("CALENDAR_UNAVAILABLE") from None

    def close(self):
        """Detach only; the caller retains and closes the original runtime."""
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise MonitorCalendarClientError("WRONG_OWNER")
        self._closed = True
