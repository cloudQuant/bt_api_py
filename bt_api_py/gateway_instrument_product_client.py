"""Borrowed owner-thread authenticated ProductID client with conservative TTL.

The caller owns the already-started runtime and must maintain and close it.
Neither a product observation nor its local deadline proves venue continuity.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from dataclasses import dataclass

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_instrument_product_codec import (
    BEGIN,
    CANCEL,
    OPERATIONS,
    STATUS,
    validate_response,
)
from bt_api_py.service_runtime import (
    RegisteredBinding,
    ServiceClientRuntime,
    _bound_binding_values,
    _bound_runtime_values,
)


class InstrumentProductClientError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class InstrumentProductResult:
    operation_id: str
    status: str
    code: str | None
    instrument: MarketInstrumentKey
    product_id: str | None = None
    session_generation: str | None = None
    connection_generation: int | None = None
    trading_day: str | None = None
    completed_at_utc: str | None = None
    valid_until_monotonic: float | None = None


class GatewayInstrumentProductClient:
    """One authenticated registration; renewal is accepted only with runtime lineage."""

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
            or any(type(v) is not str or not v for v in (service_id, instance_id, scope_ref))
        ):
            raise InstrumentProductClientError("INVALID_CONFIGURATION")
        self._runtime = self._original_runtime = runtime
        self._identity, self._scope, self._peer = identity, scope, peer
        self._service_id, self._instance_id, self._scope_ref = service_id, instance_id, scope_ref
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._closed, self._jobs, self._terminal = False, {}, set()
        try:
            _, self._signature, self._lineage, _ = self._observe()
        except Exception:
            raise InstrumentProductClientError("BINDING_UNAVAILABLE") from None

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
        candidates = [
            b
            for b in runtime.bindings
            if type(b) is RegisteredBinding
            and b.active
            and b.mount == "upstream.instrument_products"
            and b.scope_ref == self._scope_ref
        ]
        if len(candidates) != 1:
            raise ValueError
        b = candidates[0]
        if (
            b.service_id != self._service_id
            or b.instance_id != self._instance_id
            or b.scope != self._scope
            or b.operations != OPERATIONS
            or b.remote_lease_inactive
            or not runtime._has_current_registration_observation(b)
        ):
            raise ValueError
        now = runtime._monotonic_now()
        if type(now) not in (int, float) or not math.isfinite(now) or now >= b.lease_deadline:
            raise ValueError
        proof = runtime.authenticated_peer_for_binding(b)
        if (
            type(proof) is not tuple
            or len(proof) != 2
            or proof[0] != self._peer
            or type(proof[1]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", proof[1]) is None
        ):
            raise ValueError
        registration = runtime._registration_lineage.get(id(b))
        if type(registration) is not tuple or len(registration) not in (9, 10):
            raise ValueError
        lineage = registration[2:9]
        signature = (
            tuple(
                (k, v)
                for k, v in _bound_binding_values(b)
                if k not in {"expires_at", "lease_deadline", "renewal_due_at"}
            ),
            _bound_runtime_values(runtime),
            proof,
        )
        transport = runtime._transports.get((b.service_id, b.transport_ref))
        if transport is None:
            raise ValueError
        return b, signature, lineage, transport

    def _current(self):
        b, signature, lineage, transport = self._observe()
        if (
            signature != self._signature
            or len(lineage) != len(self._lineage)
            or any(a is not b for a, b in zip(lineage, self._lineage, strict=True))
        ):
            raise ValueError
        return b, transport

    def _call(self, operation, operation_id, instrument=None):
        if type(operation_id) is not str or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
            raise InstrumentProductClientError("INVALID_REQUEST")
        if operation == BEGIN and type(instrument) is not MarketInstrumentKey:
            raise InstrumentProductClientError("INVALID_REQUEST")
        try:
            b, transport = self._current()
            job = self._jobs.get(operation_id)
            if operation == BEGIN:
                key = MarketInstrumentKey.from_dict(instrument.to_dict())
                if job is not None and job != key:
                    raise InstrumentProductClientError("OPERATION_CONFLICT")
                if job is None:
                    if len(self._jobs) >= 128:
                        raise InstrumentProductClientError("CAPACITY_EXCEEDED")
                    self._jobs[operation_id] = key
            else:
                if job is None:
                    raise InstrumentProductClientError("UNKNOWN_OPERATION")
                key = job
            arguments = {"operation_id": operation_id}
            if operation == BEGIN:
                arguments["instrument"] = key.to_dict()
            # A new call can observe pending or have an unknown outcome; only
            # a newly confirmed terminal reply permits local retirement.
            self._terminal.discard(operation_id)
            current, _ = self._current()
            if current is not b:
                raise ValueError
            started = time.monotonic()
            response = self._runtime.call(
                "upstream.instrument_products",
                operation,
                arguments,
                account_ref=self._scope.account_ref,
                scope_ref=self._scope_ref,
                _expected_binding=b,
                _expected_binding_values=_bound_binding_values(b),
                _expected_runtime_values=_bound_runtime_values(self._runtime),
                _expected_transport=transport,
                _expected_peer=self._peer,
                _expected_fingerprint=self._signature[2][1],
            )
            current, _ = self._current()
            if current is not b:
                raise ValueError
            value = validate_response(
                response, expected_operation_id=operation_id, expected_instrument=key
            )
            snap = value["snapshot"]
            if snap is None:
                result = InstrumentProductResult(operation_id, value["status"], value["code"], key)
            else:
                expiry = started + snap["remaining_seconds"]
                if not math.isfinite(expiry) or time.monotonic() >= expiry:
                    raise InstrumentProductClientError("OBSERVATION_EXPIRED")
                result = InstrumentProductResult(
                    operation_id,
                    "ready",
                    None,
                    key,
                    snap["product_id"],
                    snap["session_generation"],
                    snap["connection_generation"],
                    snap["trading_day"],
                    snap["completed_at_utc"],
                    expiry,
                )
            current, _ = self._current()
            if current is not b:
                raise ValueError
            if value["status"] in {"ready", "failed", "cancelled", "not_found"}:
                self._terminal.add(operation_id)
            return result
        except InstrumentProductClientError:
            raise
        except Exception:
            raise InstrumentProductClientError("PRODUCT_UNAVAILABLE") from None

    def begin(self, operation_id, instrument):
        return self._call(BEGIN, operation_id, instrument)

    def status(self, operation_id):
        return self._call(STATUS, operation_id)

    def cancel(self, operation_id):
        return self._call(CANCEL, operation_id)

    def retire(self, operation_id):
        """Forget a locally observed terminal job; the server retains its own record."""
        try:
            self._current()
            if operation_id not in self._terminal:
                raise InstrumentProductClientError("OPERATION_NOT_TERMINAL")
            self._terminal.remove(operation_id)
            del self._jobs[operation_id]
        except InstrumentProductClientError:
            raise
        except Exception:
            raise InstrumentProductClientError("PRODUCT_UNAVAILABLE") from None

    def close(self):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise InstrumentProductClientError("WRONG_OWNER")
        self._closed = True
        self._jobs.clear()
        self._terminal.clear()
