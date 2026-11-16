"""Strategy diagnostics over the existing authenticated Gateway runtime.

These observations never prove continuity or authorize trading. Receive stamps
are exposed as source diagnostics and are never compared to a local clock.
Local observation tokens recheck only the client binding and peer; they do not
re-read the server's instrument status or prove native source continuity.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, replace

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.instrument_status_forwarding import GatewayInstrumentStatusProjection

from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_instrument_status_query import (
    OPERATION,
    OPERATIONS,
    REJECTION_CODES,
    decode_query,
    encode_observation,
    validate_response,
)
from bt_api_py.service_runtime import RegisteredBinding, _bound_binding_values
from bt_api_py.service_runtime_thread import (
    OwnerOperationError,
    ThreadedRuntimeError,
    _copy_wire,
    _timeout,
)

_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def _validate_route(account_ref, scope_ref, route, peer):
    if (
        type(route) is not GatewayClientRoute
        or type(peer) is not WireIdentity
        or peer.role is not WireRole.SERVICE
        or peer.delegate_ref is not None
        or account_ref != route.scope.account_ref
        or scope_ref != route.scope_ref
        or route.identity.actor_ref != route.scope.strategy_ref
        or route.identity.delegate_ref is not None
    ):
        raise ValueError("INVALID_INSTRUMENT_STATUS_ROUTE")


def _detached_route(route, peer):
    return (
        replace(
            route,
            scope=WireScope(**vars(route.scope)),
            identity=WireIdentity(**vars(route.identity)),
        ),
        WireIdentity(**vars(peer)),
    )


@dataclass(frozen=True, slots=True)
class GatewayInstrumentStatusQueryResult:
    """A detached Gateway observation or a finite unavailable result."""

    status: str
    instrument: MarketInstrumentKey
    code: str | None = None
    registration_id: str | None = None
    service_epoch: str | None = None
    owner_generation: str | None = None
    projection: GatewayInstrumentStatusProjection | None = None
    decision_eligible: bool = False
    continuity_proven: bool = False
    authorizes_trading: bool = False
    product_mapping: str = "NOT_PROVEN"

    def __post_init__(self):
        if (
            type(self.instrument) is not MarketInstrumentKey
            or self.decision_eligible is not False
            or self.continuity_proven is not False
            or self.authorizes_trading is not False
            or self.product_mapping != "NOT_PROVEN"
        ):
            raise ValueError("INVALID_INSTRUMENT_STATUS_RESULT")
        if self.status == "UNAVAILABLE":
            if (
                type(self.code) is not str
                or self.code not in REJECTION_CODES
                or any(
                    value is not None
                    for value in (
                        self.registration_id,
                        self.service_epoch,
                        self.owner_generation,
                        self.projection,
                    )
                )
            ):
                raise ValueError("INVALID_INSTRUMENT_STATUS_RESULT")
        elif self.status == "OBSERVED":
            if self.code is not None:
                raise ValueError("INVALID_INSTRUMENT_STATUS_RESULT")
            encode_observation(
                instrument=self.instrument,
                registration_id=self.registration_id,
                service_epoch=self.service_epoch,
                owner_generation=self.owner_generation,
                projection=self.projection,
            )
        else:
            raise ValueError("INVALID_INSTRUMENT_STATUS_RESULT")

    @classmethod
    def from_wire(cls, wire, *, instrument):
        value = validate_response(wire, expected_instrument=instrument)
        if value["status"] == "REQUEST_REJECTED":
            return cls("UNAVAILABLE", instrument, code=value["code"])
        projection = dict(value["projection"])
        projection.pop("schema")
        return cls(
            "OBSERVED",
            MarketInstrumentKey.from_dict(value["instrument"]),
            registration_id=value["registration_id"],
            service_epoch=value["service_epoch"],
            owner_generation=value["owner_generation"],
            projection=GatewayInstrumentStatusProjection(**projection),
        )


def detached_query_result(result, *, expected_instrument):
    """Revalidate and detach a diagnostic result without granting authority."""
    try:
        if type(result) is not GatewayInstrumentStatusQueryResult:
            raise ValueError
        if type(expected_instrument) is not MarketInstrumentKey:
            raise ValueError
        expected = decode_query({"instrument": expected_instrument.to_dict()})
        if type(result.instrument) is not MarketInstrumentKey:
            raise ValueError
        instrument = decode_query({"instrument": result.instrument.to_dict()})
        if (
            instrument != expected
            or type(result.status) is not str
            or type(result.product_mapping) is not str
        ):
            raise ValueError

        projection = result.projection
        if projection is not None:
            if (
                type(projection) is not GatewayInstrumentStatusProjection
                or projection.authorizes_trading is not False
                or set(vars(projection))
                != set(GatewayInstrumentStatusProjection.__dataclass_fields__)
            ):
                raise ValueError
            projection = GatewayInstrumentStatusProjection(**vars(projection))
        checked = GatewayInstrumentStatusQueryResult(
            status=result.status,
            instrument=instrument,
            code=result.code,
            registration_id=result.registration_id,
            service_epoch=result.service_epoch,
            owner_generation=result.owner_generation,
            projection=projection,
            decision_eligible=result.decision_eligible,
            continuity_proven=result.continuity_proven,
            authorizes_trading=result.authorizes_trading,
            product_mapping=result.product_mapping,
        )
        if checked.status == "UNAVAILABLE":
            wire = {"status": "REQUEST_REJECTED", "code": checked.code}
        else:
            wire = encode_observation(
                instrument=instrument,
                registration_id=checked.registration_id,
                service_epoch=checked.service_epoch,
                owner_generation=checked.owner_generation,
                projection=checked.projection,
            )
        return GatewayInstrumentStatusQueryResult.from_wire(wire, instrument=expected)
    except Exception:
        raise ValueError("INVALID_INSTRUMENT_STATUS_RESULT") from None


class GatewayInstrumentStatusOwner:
    """One owner-thread bridge; no new connection or independent registration."""

    def __init__(self, *, account_ref, scope_ref, route, peer):
        _validate_route(account_ref, scope_ref, route, peer)
        route, peer = _detached_route(route, peer)
        self._account_ref, self._scope_ref, self._route, self._peer = (
            account_ref,
            scope_ref,
            route,
            peer,
        )
        self._pid = os.getpid()
        self._thread = self._runtime = None
        self._original_runtime = None
        self._closed = False
        self._issued = {}

    @property
    def cleanup_uncertainties(self):
        return 0

    def _check(self):
        if os.getpid() != self._pid:
            raise OwnerOperationError("WRONG_PROCESS")
        if self._thread is not None and threading.current_thread() is not self._thread:
            raise OwnerOperationError("WRONG_OWNER_THREAD")
        if self._closed or self._runtime is None or self._runtime is not self._original_runtime:
            raise OwnerOperationError("INSTRUMENT_STATUS_UNAVAILABLE")

    def start(self, runtime):
        if os.getpid() != self._pid:
            raise OwnerOperationError("WRONG_PROCESS")
        if self._closed or self._runtime is not None or self._thread is not None:
            raise OwnerOperationError("INSTRUMENT_STATUS_NOT_NEW")
        self._runtime, self._original_runtime, self._thread = (
            runtime,
            runtime,
            threading.current_thread(),
        )

    def _binding(self):
        try:
            self._check()
            runtime, route = self._runtime, self._route
            matches = [
                b
                for b in runtime.bindings
                if type(b) is RegisteredBinding and b.active and b.mount == "broker.direct_write"
            ]
            if len(matches) != 1:
                raise ValueError
            b = matches[0]
            proof = runtime.authenticated_peer_for_binding(b)
            now = runtime._monotonic_now()
            if (
                type(proof) is not tuple
                or len(proof) != 2
                or proof[0] != self._peer
                or type(proof[1]) is not str
                or _FINGERPRINT.fullmatch(proof[1]) is None
                or type(now) not in (int, float)
                or not math.isfinite(now)
                or type(b.lease_deadline) not in (int, float)
                or not math.isfinite(b.lease_deadline)
                or b.remote_lease_inactive
                or now >= b.lease_deadline
                or b.service_id != route.service_id
                or b.instance_id != route.instance_id
                or b.scope_ref != self._scope_ref
                or b.scope.account_ref != self._account_ref
                or b.scope != route.scope
                or runtime.run_id != route.scope.run_id
                or runtime.identity != route.identity
                or not b.operations >= OPERATIONS
            ):
                raise ValueError
            signature = (
                _bound_binding_values(b),
                tuple(vars(WireIdentity(**vars(proof[0]))).items()),
                proof[1],
            )
            self._check()
            if runtime is not self._original_runtime:
                raise ValueError
            return b, signature
        except Exception:
            raise OwnerOperationError("INSTRUMENT_STATUS_UNAVAILABLE") from None

    def invoke(self, operation, arguments):
        self._check()
        if type(arguments) is not dict:
            raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
        if operation == "validate":
            if (
                set(arguments) != {"registration_id", "service_epoch", "observation_id"}
                or type(arguments["registration_id"]) is not str
                or type(arguments["service_epoch"]) is not str
                or type(arguments["observation_id"]) is not str
                or re.fullmatch(r"[0-9a-f]{32}", arguments["observation_id"]) is None
            ):
                raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
            binding, proof = self._binding()
            key = (arguments["registration_id"], arguments["service_epoch"])
            issued = self._issued.get(arguments["observation_id"])
            if (
                issued is None
                or issued[0] is not binding
                or issued[1] != proof
                or key != (binding.registration_id, binding.service_epoch)
            ):
                raise OwnerOperationError("INSTRUMENT_STATUS_UNAVAILABLE")
            self._check()
            return {"valid": True, "peer_fingerprint": proof[2]}
        if operation != "query":
            raise OwnerOperationError("INVALID_OWNER_ARGUMENTS")
        try:
            key = decode_query(arguments)
        except ValueError:
            raise OwnerOperationError("INVALID_OWNER_ARGUMENTS") from None
        before, proof = self._binding()
        try:
            self._check()
            raw = self._original_runtime.call(
                "broker.direct_write",
                OPERATION,
                {"instrument": key.to_dict()},
                account_ref=self._account_ref,
                scope_ref=self._scope_ref,
            )
            after, after_proof = self._binding()
            if after is not before or after_proof != proof:
                raise ValueError
            value = validate_response(
                _copy_wire(raw),
                expected_instrument=key,
                expected_registration_id=before.registration_id,
                expected_service_epoch=before.service_epoch,
            )
            observation_id = None
            if value["status"] == "OBSERVED":
                for _ in range(4):
                    candidate = uuid.uuid4().hex
                    if candidate not in self._issued:
                        observation_id = candidate
                        break
                if observation_id is None:
                    raise ValueError
                if len(self._issued) >= 128:
                    self._issued.pop(next(iter(self._issued)))
                self._issued[observation_id] = (before, proof)
            self._check()
            return {"wire": value, "observation_id": observation_id}
        except Exception:
            raise OwnerOperationError("INSTRUMENT_STATUS_UNAVAILABLE") from None

    def close(self):
        if os.getpid() != self._pid:
            raise OwnerOperationError("WRONG_PROCESS")
        if self._thread is not None and threading.current_thread() is not self._thread:
            raise OwnerOperationError("WRONG_OWNER_THREAD")
        self._closed = True
        self._runtime = None
        self._original_runtime = None
        self._issued.clear()


class GatewayStrategyInstrumentStatusClient:
    """Typed, bounded polling on the already-owned threaded service runtime."""

    def __init__(self, runtime, *, route, peer):
        _validate_route(route.scope.account_ref, route.scope_ref, route, peer)
        route, peer = _detached_route(route, peer)
        self._runtime, self._route, self._peer = runtime, route, peer
        self._original_runtime = runtime
        self._pid, self._lock, self._pending = os.getpid(), threading.Lock(), None

    def query(self, instrument, *, timeout_seconds=5.0):
        if type(instrument) is not MarketInstrumentKey:
            raise ValueError("INVALID_INSTRUMENT_STATUS_REQUEST")
        key = decode_query({"instrument": instrument.to_dict()})
        timeout = _timeout(timeout_seconds)
        if os.getpid() != self._pid:
            raise ThreadedRuntimeError("WRONG_PROCESS")
        if not self._lock.acquire(blocking=False):
            return GatewayInstrumentStatusQueryResult(
                "UNAVAILABLE", key, code="INSTRUMENT_STATUS_UNAVAILABLE"
            )
        deadline = time.monotonic() + timeout

        def remaining():
            left = deadline - time.monotonic()
            if left <= 0:
                raise ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING")
            return left

        def unavailable(code="INSTRUMENT_STATUS_UNAVAILABLE"):
            return GatewayInstrumentStatusQueryResult("UNAVAILABLE", key, code=code)

        def check_runtime():
            if self._runtime is not self._original_runtime:
                raise ThreadedRuntimeError("OWNER_RUNTIME_CHANGED")

        try:
            try:
                check_runtime()
            except ThreadedRuntimeError:
                return unavailable()
            if self._pending is None:
                try:
                    remaining()
                    check_runtime()
                    handle = self._original_runtime.submit_owner(
                        "instrument_status.query", {"instrument": key.to_dict()}
                    )
                    self._pending = {"key": key, "query": handle, "wire": None, "validation": None}
                    check_runtime()
                except Exception:
                    return unavailable()
            pending = self._pending
            if pending["key"] != key:
                return unavailable()
            try:
                if pending["wire"] is None:
                    pending["wire"] = pending["query"].result(timeout_seconds=remaining())
                    check_runtime()
                if pending["validation"] is not None:
                    pending["validation"].result(timeout_seconds=remaining())
                    check_runtime()
                    pending["validation"] = None
                observation = pending["wire"]
                if type(observation) is not dict or set(observation) != {"wire", "observation_id"}:
                    raise ValueError
                wire = validate_response(observation["wire"], expected_instrument=key)
                if wire["status"] == "REQUEST_REJECTED":
                    if observation["observation_id"] is not None:
                        raise ValueError
                    self._pending = None
                    return unavailable(wire["code"])
                if (
                    type(observation["observation_id"]) is not str
                    or re.fullmatch(r"[0-9a-f]{32}", observation["observation_id"]) is None
                ):
                    raise ValueError
                remaining()
                check_runtime()
                handle = self._original_runtime.submit_owner(
                    "instrument_status.validate",
                    {
                        "registration_id": wire["registration_id"],
                        "service_epoch": wire["service_epoch"],
                        "observation_id": observation["observation_id"],
                    },
                )
                pending["validation"] = handle
                check_runtime()
                validated = handle.result(timeout_seconds=remaining())
                check_runtime()
                if (
                    type(validated) is not dict
                    or set(validated) != {"valid", "peer_fingerprint"}
                    or validated["valid"] is not True
                    or type(validated["peer_fingerprint"]) is not str
                    or _FINGERPRINT.fullmatch(validated["peer_fingerprint"]) is None
                ):
                    raise ValueError
                remaining()
                snapshot = self._original_runtime.status()
                check_runtime()
                matches = [
                    b for b in snapshot.bindings if b.mount == "broker.direct_write" and b.active
                ]
                if (
                    snapshot.state != "running"
                    or not snapshot.healthy
                    or len(matches) != 1
                    or matches[0].registration_id != wire["registration_id"]
                    or matches[0].service_epoch != wire["service_epoch"]
                    or matches[0].service_id != self._route.service_id
                    or matches[0].instance_id != self._route.instance_id
                    or matches[0].scope_ref != self._route.scope_ref
                    or matches[0].scope != self._route.scope
                    or matches[0].authenticated_peer != self._peer
                    or type(matches[0].server_key_fingerprint) is not str
                    or matches[0].server_key_fingerprint != validated["peer_fingerprint"]
                    or snapshot.identity != self._route.identity
                    or snapshot.run_id != self._route.scope.run_id
                ):
                    raise ValueError
                result = GatewayInstrumentStatusQueryResult.from_wire(wire, instrument=key)
                remaining()
                check_runtime()
            except ThreadedRuntimeError as exc:
                if exc.code not in {
                    "OWNER_TIMEOUT_QUEUED",
                    "OWNER_TIMEOUT_RUNNING",
                    "OWNER_RUNTIME_CHANGED",
                }:
                    self._pending = None
                return unavailable()
            except Exception:
                self._pending = None
                return unavailable()
            self._pending = None
            return result
        finally:
            self._lock.release()
