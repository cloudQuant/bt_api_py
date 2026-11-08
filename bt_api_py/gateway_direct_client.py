"""Strategy-side client for the Gateway direct-command storage and dispatch RPCs.

The host port is trusted to implement atomic, durable conflict checks and
monotonic observations; readback detects mismatches but cannot prove fsync.
The client never creates a runtime, obtains provider credentials, retries an
operation, or falls back to direct trading. A new run may read or release an old
intent under Registry authority; it cannot dispatch that old intent. Queue
receipts are not native sends, and remaining TTL is only a remote observation.
REQUEST_DENIED and TRANSPORT_NOT_STARTED describe the current RPC only; neither
marks an older order rejected nor proves it was not sent. UNKNOWN retains the
original durable intent for an explicit query by its original key.
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from bt_api_base.gateway_direct_protocol import (
    GatewayCommandBinding,
    GatewayCommandRecord,
    canonical_gateway_payload,
    gateway_payload_digest,
)
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._contracts.models import OrderRequest
from bt_api_py.gateway_order_contract import decode_gateway_order_request
from bt_api_py.service_runtime import ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime


@dataclass(frozen=True, slots=True)
class GatewayClientRoute:
    mount: str
    service_id: str
    instance_id: str
    scope_ref: str
    scope: WireScope
    identity: WireIdentity

    def __post_init__(self) -> None:
        if (
            type(self.mount) is not str
            or self.mount != "broker.direct_write"
            or not all(_ref(v) for v in (self.service_id, self.instance_id, self.scope_ref))
            or type(self.scope) is not WireScope
            or type(self.identity) is not WireIdentity
            or self.identity.role is not WireRole.STRATEGY_CLIENT
            or self.scope.account_ref is None
            or self.scope.strategy_ref is None
            or self.scope.run_id is None
            or self.scope.account_refs
            or self.scope.portfolio_ref is not None
            or self.scope.manual_book_ref is not None
            or self.scope.group_id is not None
            or self.scope.leg_id is not None
        ):
            raise ValueError("INVALID_ROUTE")


@dataclass(frozen=True, slots=True)
class GatewayOrderIntent:
    binding: GatewayCommandBinding
    route: GatewayClientRoute
    request_key: str
    canonical_payload: str
    digest: str
    ttl_seconds: float


@dataclass(frozen=True, slots=True)
class GatewayReleasePlan:
    request_key: str
    reservation_id: str
    digest: str
    operation_id: str
    expected_version: int


@dataclass(frozen=True, slots=True)
class GatewayClientResult:
    outcome: str
    record: GatewayCommandRecord | None = None
    code: str | None = None
    marker_id: str | None = None


class GatewayClientHostPort(Protocol):
    """Trusted durable host storage; writes must be atomic and conflict checked.

    ``save_intent`` must retain the first intent for a key, ``save_record``
    must enforce monotonic version/identity across processes, and ``save_release``
    must retain the first release plan. ``commit_pre_dispatch`` must durably
    bind its nonempty marker to the exact key and record before returning and
    return the same marker for repeated calls on that binding, including after
    restart.
    Loading after a save must return the committed value or raise.
    """

    def save_intent(self, intent: GatewayOrderIntent) -> None: ...
    def load_intent(self, request_key: str) -> GatewayOrderIntent | None: ...
    def save_record(self, request_key: str, record: GatewayCommandRecord) -> None: ...
    def load_record(self, request_key: str) -> GatewayCommandRecord | None: ...
    def commit_pre_dispatch(self, request_key: str, record: GatewayCommandRecord) -> str: ...
    def save_release(self, plan: GatewayReleasePlan) -> None: ...
    def load_release(self, request_key: str) -> GatewayReleasePlan | None: ...


def _ref(value: object) -> bool:
    if type(value) is not str or not value or value != value.strip() or len(value) > 256:
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return True


_BINDING_FIELDS = ("journal_id", "account_scope", "strategy_scope", "principal")
_ROUTE_FIELDS = ("mount", "service_id", "instance_id", "scope_ref", "scope", "identity")
_SCOPE_FIELDS = (
    "execution_domain_ref",
    "account_ref",
    "account_refs",
    "strategy_ref",
    "run_id",
    "manual_book_ref",
    "portfolio_ref",
    "group_id",
    "leg_id",
)
_IDENTITY_FIELDS = ("role", "principal_ref", "actor_ref", "delegate_ref")
_INTENT_FIELDS = (
    "schema_version",
    "binding",
    "route",
    "request_key",
    "canonical_payload",
    "digest",
    "ttl_seconds",
)
_RELEASE_FIELDS = (
    "schema_version",
    "request_key",
    "reservation_id",
    "digest",
    "operation_id",
    "expected_version",
)


def _exact_mapping(value: object, fields: tuple[str, ...]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise ValueError("INVALID_GATEWAY_MAPPING")
    return value


def _check_binding_route(binding: object, route: object) -> None:
    if type(binding) is not GatewayCommandBinding or type(route) is not GatewayClientRoute:
        raise ValueError("INVALID_CLIENT_BINDING")
    GatewayCommandBinding(*(getattr(binding, key) for key in _BINDING_FIELDS))
    if type(route.scope) is not WireScope or type(route.identity) is not WireIdentity:
        raise ValueError("INVALID_CLIENT_ROUTE")
    WireScope(**{key: getattr(route.scope, key) for key in _SCOPE_FIELDS})
    WireIdentity(**{key: getattr(route.identity, key) for key in _IDENTITY_FIELDS})
    GatewayClientRoute(
        route.mount,
        route.service_id,
        route.instance_id,
        route.scope_ref,
        route.scope,
        route.identity,
    )
    if (
        route.scope.account_ref != binding.account_scope
        or route.scope.strategy_ref != binding.strategy_scope
        or route.identity.principal_ref != binding.principal
        or route.identity.actor_ref != binding.strategy_scope
    ):
        raise ValueError("INVALID_CLIENT_ROUTE")


def _check_intent(intent: object) -> GatewayOrderIntent:
    if type(intent) is not GatewayOrderIntent:
        raise ValueError("INVALID_HOST_INTENT")
    _check_binding_route(intent.binding, intent.route)
    if (
        not _ref(intent.request_key)
        or type(intent.canonical_payload) is not str
        or len(intent.canonical_payload) > 65536
        or type(intent.digest) is not str
        or len(intent.digest) != 64
        or any(char not in "0123456789abcdef" for char in intent.digest)
        or type(intent.ttl_seconds) not in (int, float)
        or not 0 < intent.ttl_seconds <= 30
        or not math.isfinite(intent.ttl_seconds)
    ):
        raise ValueError("INVALID_HOST_INTENT")
    try:
        if len(intent.canonical_payload.encode("utf-8", errors="strict")) > 65536:
            raise ValueError("INVALID_HOST_INTENT")
    except UnicodeError:
        raise ValueError("INVALID_HOST_INTENT") from None
    payload = json.loads(intent.canonical_payload)
    if (
        canonical_gateway_payload(payload) != intent.canonical_payload
        or gateway_payload_digest(payload) != intent.digest
        or decode_gateway_order_request(payload, intent.binding.account_scope).idempotency_key
        != intent.request_key
    ):
        raise ValueError("INVALID_HOST_INTENT")
    return intent


def build_gateway_order_intent(
    binding: GatewayCommandBinding,
    route: GatewayClientRoute,
    request: OrderRequest,
    *,
    ttl_seconds: float = 10.0,
) -> GatewayOrderIntent:
    """Build a validated immutable intent without establishing authorization."""
    _check_binding_route(binding, route)
    if type(request) is not OrderRequest or type(ttl_seconds) not in (int, float):
        raise ValueError("INVALID_INTENT")
    if not 0 < ttl_seconds <= 30 or not math.isfinite(ttl_seconds):
        raise ValueError("INVALID_INTENT")
    payload = request.to_dict()
    parsed = decode_gateway_order_request(payload, binding.account_scope)
    return _check_intent(
        GatewayOrderIntent(
            binding,
            route,
            parsed.idempotency_key,
            canonical_gateway_payload(payload),
            gateway_payload_digest(payload),
            float(ttl_seconds),
        )
    )


def gateway_intent_to_mapping(intent: GatewayOrderIntent) -> dict[str, Any]:
    """Return a detached schema-v1 persistence mapping."""
    _check_intent(intent)
    route = intent.route
    return {
        "schema_version": 1,
        "binding": {key: getattr(intent.binding, key) for key in _BINDING_FIELDS},
        "route": {
            "mount": route.mount,
            "service_id": route.service_id,
            "instance_id": route.instance_id,
            "scope_ref": route.scope_ref,
            "scope": {
                key: list(route.scope.account_refs)
                if key == "account_refs"
                else getattr(route.scope, key)
                for key in _SCOPE_FIELDS
            },
            "identity": {
                key: route.identity.role.value if key == "role" else getattr(route.identity, key)
                for key in _IDENTITY_FIELDS
            },
        },
        "request_key": intent.request_key,
        "canonical_payload": intent.canonical_payload,
        "digest": intent.digest,
        "ttl_seconds": intent.ttl_seconds,
    }


def gateway_intent_from_mapping(
    raw: Mapping[str, Any], *, expected_binding: GatewayCommandBinding | None = None
) -> GatewayOrderIntent:
    """Decode a strict schema-v1 intent and optionally pin its binding."""
    data = _exact_mapping(raw, _INTENT_FIELDS)
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("INVALID_GATEWAY_SCHEMA")
    binding_data = _exact_mapping(data["binding"], _BINDING_FIELDS)
    binding = GatewayCommandBinding(**dict(binding_data))
    if expected_binding is not None and (
        type(expected_binding) is not GatewayCommandBinding or binding != expected_binding
    ):
        raise ValueError("INVALID_CLIENT_BINDING")
    route_data = _exact_mapping(data["route"], _ROUTE_FIELDS)
    scope_data = _exact_mapping(route_data["scope"], _SCOPE_FIELDS)
    identity_data = _exact_mapping(route_data["identity"], _IDENTITY_FIELDS)
    if type(scope_data["account_refs"]) is not list or type(identity_data["role"]) is not str:
        raise ValueError("INVALID_GATEWAY_MAPPING")
    scope = WireScope(**dict(scope_data))
    try:
        role = WireRole(identity_data["role"])
    except ValueError:
        raise ValueError("INVALID_GATEWAY_MAPPING") from None
    identity = WireIdentity(
        role=role,
        principal_ref=identity_data["principal_ref"],
        actor_ref=identity_data["actor_ref"],
        delegate_ref=identity_data["delegate_ref"],
    )
    route = GatewayClientRoute(
        route_data["mount"],
        route_data["service_id"],
        route_data["instance_id"],
        route_data["scope_ref"],
        scope,
        identity,
    )
    return _check_intent(
        GatewayOrderIntent(
            binding,
            route,
            data["request_key"],
            data["canonical_payload"],
            data["digest"],
            data["ttl_seconds"],
        )
    )


def _check_release(plan: object) -> GatewayReleasePlan:
    if type(plan) is not GatewayReleasePlan or (
        not all(_ref(value) for value in (plan.request_key, plan.reservation_id, plan.operation_id))
        or type(plan.digest) is not str
        or len(plan.digest) != 64
        or any(char not in "0123456789abcdef" for char in plan.digest)
        or type(plan.expected_version) is not int
        or plan.expected_version < 1
    ):
        raise ValueError("INVALID_RELEASE")
    return plan


def gateway_release_to_mapping(plan: GatewayReleasePlan) -> dict[str, Any]:
    _check_release(plan)
    return {"schema_version": 1, **{key: getattr(plan, key) for key in _RELEASE_FIELDS[1:]}}


def gateway_release_from_mapping(raw: Mapping[str, Any]) -> GatewayReleasePlan:
    data = _exact_mapping(raw, _RELEASE_FIELDS)
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("INVALID_GATEWAY_SCHEMA")
    return _check_release(GatewayReleasePlan(**{key: data[key] for key in _RELEASE_FIELDS[1:]}))


def _route_equivalent(left: GatewayClientRoute, right: GatewayClientRoute) -> bool:
    return (
        left.mount,
        left.service_id,
        left.instance_id,
        left.scope_ref,
        left.identity,
        left.scope.execution_domain_ref,
        left.scope.account_ref,
        left.scope.strategy_ref,
    ) == (
        right.mount,
        right.service_id,
        right.instance_id,
        right.scope_ref,
        right.identity,
        right.scope.execution_domain_ref,
        right.scope.account_ref,
        right.scope.strategy_ref,
    )


def _not_started(code: str) -> GatewayClientResult:
    return GatewayClientResult("TRANSPORT_NOT_STARTED", code=code)


_REQUEST_DENIALS = frozenset(
    {
        "INVALID_REQUEST",
        "SCOPE_DENIED",
        "NOT_FOUND",
        "REQUEST_CONFLICT",
        "VERSION_CONFLICT",
        "ADMISSION_DENIED",
        "DISPATCH_UNAVAILABLE",
        "DISPATCH_QUEUE_FULL",
        "DISPATCH_QUEUE_CLOSING",
        "DISPATCH_QUEUE_BUSY",
        "DISPATCH_QUEUE_CONFLICT",
    }
)
_NOT_STARTED_ERRORS = frozenset(
    {
        "NOT_READY",
        "QUEUE_FULL",
        "TIMEOUT_BEFORE_START",
        "CLOSED_BEFORE_START",
        "WRONG_PROCESS",
        "OWNER_THREAD_REENTRY",
        "INVALID_ARGUMENTS",
    }
)
_OBSERVATION_FIELDS = frozenset(
    {"owner_generation", "generation_current", "remaining_ttl_seconds", "expiry_pending"}
)


def validate_gateway_record_progress(
    previous: GatewayCommandRecord | None, current: GatewayCommandRecord
) -> None:
    """Check stable identity and monotonic durable observations."""
    if type(current) is not GatewayCommandRecord:
        raise ValueError("INVALID_HOST_RECORD")
    for record in (current,) if previous is None else (previous, current):
        if type(record) is not GatewayCommandRecord:
            raise ValueError("INVALID_HOST_RECORD")
        if (
            GatewayCommandRecord.from_mapping(record.to_mapping(), expected_binding=record.binding)
            != record
        ):
            raise ValueError("INVALID_HOST_RECORD")
    if previous is None:
        return
    if (
        current.binding != previous.binding
        or current.request_key != previous.request_key
        or current.reservation_id != previous.reservation_id
        or current.digest != previous.digest
        or current.reservation_generation != previous.reservation_generation
        or current.version < previous.version
    ):
        raise ValueError("OBSERVATION_CONFLICT")
    if current.version == previous.version:
        old, new = previous.to_mapping(), current.to_mapping()
        for field in _OBSERVATION_FIELDS:
            old.pop(field)
            new.pop(field)
        if previous.state == "EXPIRED" and previous.release_operation_id is None:
            old["release_operation_id"] = new["release_operation_id"]
            old["release_expected_version"] = new["release_expected_version"]
        if canonical_gateway_payload(old) != canonical_gateway_payload(new):
            raise ValueError("OBSERVATION_CONFLICT")
    else:
        if previous.state in {"SENT", "NOT_SENT", "UNKNOWN", "RELEASED", "EXPIRED"}:
            raise ValueError("OBSERVATION_CONFLICT")
        if previous.native_identity is not None and (
            current.native_identity is None
            or canonical_gateway_payload(current.native_identity.to_dict())
            != canonical_gateway_payload(previous.native_identity.to_dict())
            or current.strong_identity_key_digests != previous.strong_identity_key_digests
        ):
            raise ValueError("OBSERVATION_CONFLICT")


class GatewayDirectClient:
    """Use one started owner-thread runtime and one explicitly trusted host."""

    def __init__(
        self,
        runtime: ThreadedServiceClientRuntime,
        *,
        binding: GatewayCommandBinding,
        route: GatewayClientRoute,
        host: GatewayClientHostPort,
    ):
        if (
            type(runtime) is not ThreadedServiceClientRuntime
            or type(binding) is not GatewayCommandBinding
        ):
            raise TypeError("INVALID_CLIENT_BINDING")
        _check_binding_route(binding, route)
        methods = (
            "save_intent",
            "load_intent",
            "save_record",
            "load_record",
            "commit_pre_dispatch",
            "save_release",
            "load_release",
        )
        if any(not callable(getattr(host, name, None)) for name in methods):
            raise TypeError("INVALID_HOST_PORT")
        self._runtime, self._binding, self._route, self._host = runtime, binding, route, host
        self._pid = os.getpid()
        self._lock = threading.RLock()

    def _route_ready(
        self, operation: str, *, origin_route: GatewayClientRoute | None = None
    ) -> bool:
        try:
            snapshot = self._runtime.status()
        except Exception:
            return False
        if (
            snapshot.state != "running"
            or not snapshot.thread_alive
            or not snapshot.healthy
            or type(snapshot.identity) is not WireIdentity
            or snapshot.identity != self._route.identity
            or snapshot.run_id != self._route.scope.run_id
        ):
            return False
        permitted_scopes = [self._route.scope]
        if (
            operation in {"direct.command_status", "direct.release"}
            and origin_route is not None
            and _route_equivalent(origin_route, self._route)
            and origin_route.scope != self._route.scope
        ):
            permitted_scopes.append(origin_route.scope)
        matches = [
            b
            for b in snapshot.bindings
            if b.active is True
            and b.required is True
            and b.mount == self._route.mount
            and b.service_id == self._route.service_id
            and b.instance_id == self._route.instance_id
            and b.scope_ref == self._route.scope_ref
            and type(b.scope) is WireScope
            and b.scope in permitted_scopes
            and type(b.operations) is frozenset
            and {operation, "direct.command_status"}.issubset(b.operations)
        ]
        return len(matches) == 1

    def _intent(self, key: str) -> GatewayOrderIntent:
        intent = self._host.load_intent(key)
        _check_intent(intent)
        if (
            intent.binding != self._binding
            or not _route_equivalent(intent.route, self._route)
            or intent.request_key != key
            or not _ref(key)
        ):
            raise ValueError("INVALID_HOST_INTENT")
        return intent

    def _record(self, key: str) -> GatewayCommandRecord | None:
        record = self._host.load_record(key)
        if record is None:
            return None
        if type(record) is not GatewayCommandRecord:
            raise ValueError("INVALID_HOST_RECORD")
        checked = GatewayCommandRecord.from_mapping(
            record.to_mapping(), expected_binding=self._binding
        )
        intent = self._intent(key)
        if checked != record or checked.request_key != key or checked.digest != intent.digest:
            raise ValueError("INVALID_HOST_RECORD")
        return checked

    _validate_progress = staticmethod(validate_gateway_record_progress)

    def _rpc(
        self, operation: str, arguments: dict[str, Any], key: str, timeout_seconds: float
    ) -> GatewayClientResult:
        origin_route = None
        if operation in {"direct.command_status", "direct.release"}:
            try:
                origin_route = self._intent(key).route
            except Exception:
                return _not_started("INTENT_UNAVAILABLE")
        if not self._route_ready(operation, origin_route=origin_route):
            return _not_started("ROUTE_UNAVAILABLE")
        try:
            result = self._runtime.call(
                self._route.mount,
                operation,
                arguments,
                account_ref=self._binding.account_scope,
                scope_ref=self._route.scope_ref,
                timeout_seconds=timeout_seconds,
            )
        except ServiceRuntimeRequestNotSentError:
            return _not_started("REQUEST_NOT_SENT")
        except ThreadedRuntimeError as error:
            return (
                _not_started(error.code)
                if error.code in _NOT_STARTED_ERRORS
                else GatewayClientResult("UNKNOWN", code="RPC_OUTCOME_UNKNOWN")
            )
        except Exception:
            return GatewayClientResult("UNKNOWN", code="RPC_OUTCOME_UNKNOWN")
        try:
            if not isinstance(result, Mapping):
                raise ValueError("INVALID_REPLY")
            if result.get("status") == "REQUEST_REJECTED":
                if set(result) != {"status", "code"} or result["code"] not in _REQUEST_DENIALS:
                    raise ValueError("INVALID_REPLY")
                return GatewayClientResult("REQUEST_DENIED", code=result["code"])
            allowed = {"status", "record"} | (
                {"dispatch_queue_state"} if operation == "direct.dispatch" else set()
            )
            if set(result) != allowed or result.get("status") != "OK":
                raise ValueError("INVALID_REPLY")
            if operation == "direct.dispatch" and result["dispatch_queue_state"] not in {
                "QUEUED",
                "ALREADY_QUEUED",
            }:
                raise ValueError("INVALID_REPLY")
            record = GatewayCommandRecord.from_mapping(
                result["record"], expected_binding=self._binding
            )
            intent = self._intent(key)
            if record.request_key != key or record.digest != intent.digest:
                raise ValueError("INVALID_REPLY")
            if operation in {"direct.release", "direct.dispatch"} and (
                record.reservation_id != arguments["reservation_id"]
                or record.digest != arguments["digest"]
            ):
                raise ValueError("INVALID_REPLY")
            if operation == "direct.dispatch" and record.version < arguments["expected_version"]:
                raise ValueError("INVALID_REPLY")
            if operation == "direct.release" and (
                record.state not in {"RELEASED", "EXPIRED"}
                or record.release_operation_id != arguments["operation_id"]
                or record.release_expected_version != arguments["expected_version"]
            ):
                raise ValueError("INVALID_REPLY")
            prior = self._record(key)
            self._validate_progress(prior, record)
            self._host.save_record(key, record)
            if self._record(key) != record:
                raise ValueError("HOST_RECORD_MISMATCH")
            outcome = (
                "QUEUED"
                if operation == "direct.dispatch" and record.state == "RESERVED"
                else "OBSERVED"
            )
            return GatewayClientResult(outcome, record)
        except Exception:
            return GatewayClientResult("UNKNOWN", code="INVALID_REPLY_OR_HOST_FAILURE")

    def reserve(
        self, request: OrderRequest, *, ttl_seconds: float = 10.0, timeout_seconds: float = 5.0
    ) -> GatewayClientResult:
        if os.getpid() != self._pid:
            return _not_started("WRONG_PROCESS")
        with self._lock:
            if not self._route_ready("direct.reserve"):
                return _not_started("ROUTE_UNAVAILABLE")
            try:
                candidate = build_gateway_order_intent(
                    self._binding, self._route, request, ttl_seconds=ttl_seconds
                )
                payload = request.to_dict()
                key = candidate.request_key
                existing = self._host.load_intent(key)
                if existing is None:
                    self._host.save_intent(candidate)
                    existing = self._intent(key)
                    if existing != candidate:
                        raise ValueError("INTENT_CONFLICT")
                else:
                    existing = self._intent(key)
                    if (
                        existing.binding != candidate.binding
                        or existing.route != candidate.route
                        or existing.request_key != candidate.request_key
                        or existing.canonical_payload != candidate.canonical_payload
                        or existing.digest != candidate.digest
                    ):
                        raise ValueError("INTENT_CONFLICT")
                arguments = {
                    "request_key": key,
                    "payload": payload,
                    "ttl_seconds": existing.ttl_seconds,
                }
            except Exception:
                return _not_started("INTENT_NOT_DURABLE")
            return self._rpc("direct.reserve", arguments, key, timeout_seconds)

    def status(self, request_key: str, *, timeout_seconds: float = 5.0) -> GatewayClientResult:
        if os.getpid() != self._pid:
            return _not_started("WRONG_PROCESS")
        with self._lock:
            try:
                self._intent(request_key)
            except Exception:
                return _not_started("INTENT_UNAVAILABLE")
            return self._rpc(
                "direct.command_status", {"request_key": request_key}, request_key, timeout_seconds
            )

    def dispatch(self, request_key: str, *, timeout_seconds: float = 5.0) -> GatewayClientResult:
        if os.getpid() != self._pid:
            return _not_started("WRONG_PROCESS")
        with self._lock:
            if not self._route_ready("direct.dispatch"):
                return _not_started("ROUTE_UNAVAILABLE")
            try:
                intent = self._intent(request_key)
                record = self._record(request_key)
                if (
                    intent.route.scope.run_id != self._route.scope.run_id
                    or record is None
                    or record.state != "RESERVED"
                    or not record.generation_current
                    or record.expiry_pending
                ):
                    raise ValueError("NOT_DISPATCHABLE")
                marker = self._host.commit_pre_dispatch(request_key, record)
                if not _ref(marker):
                    raise ValueError("INVALID_MARKER")
                arguments = {
                    "request_key": request_key,
                    "reservation_id": record.reservation_id,
                    "digest": record.digest,
                    "expected_version": record.version,
                }
            except Exception:
                return _not_started("DISPATCH_NOT_COMMITTED")
            result = self._rpc("direct.dispatch", arguments, request_key, timeout_seconds)
            return GatewayClientResult(result.outcome, result.record, result.code, marker)

    def release(
        self, request_key: str, *, operation_id: str, timeout_seconds: float = 5.0
    ) -> GatewayClientResult:
        if os.getpid() != self._pid:
            return _not_started("WRONG_PROCESS")
        with self._lock:
            try:
                intent = self._intent(request_key)
                if not self._route_ready("direct.release", origin_route=intent.route):
                    return _not_started("ROUTE_UNAVAILABLE")
                record = self._record(request_key)
                if record is None or not _ref(operation_id):
                    raise ValueError("INVALID_RELEASE")
                prior = self._host.load_release(request_key)
                if prior is None:
                    plan = GatewayReleasePlan(
                        request_key,
                        record.reservation_id,
                        record.digest,
                        operation_id,
                        record.version,
                    )
                    _check_release(plan)
                    self._host.save_release(plan)
                    if self._host.load_release(request_key) != plan:
                        raise ValueError("RELEASE_NOT_DURABLE")
                else:
                    _check_release(prior)
                    if (
                        type(prior) is not GatewayReleasePlan
                        or prior.request_key != request_key
                        or prior.operation_id != operation_id
                        or prior.reservation_id != record.reservation_id
                        or prior.digest != record.digest
                        or type(prior.expected_version) is not int
                        or prior.expected_version < 1
                    ):
                        raise ValueError("RELEASE_CONFLICT")
                    plan = prior
                arguments = {
                    "request_key": request_key,
                    "reservation_id": plan.reservation_id,
                    "digest": plan.digest,
                    "expected_version": plan.expected_version,
                    "operation_id": plan.operation_id,
                }
            except Exception:
                return _not_started("RELEASE_NOT_DURABLE")
            return self._rpc("direct.release", arguments, request_key, timeout_seconds)
