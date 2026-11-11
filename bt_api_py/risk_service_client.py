"""Strategy-side client of the independent Risk service (iteration 64).

A strategy whose Gateway configuration selects Risk places orders through this
client on mount ``broker.risk_order``; it never falls back to the Gateway and
never resends a request on its own. The client depends only on ``bt_api_base``
and the SDK transport runtime; it never imports ``bt_api_risk`` (INV64-02).

Every answer is a neutral ``RiskOrderResult`` validated strictly. Outcomes of a
call are deliberately coarse:

* ``ANSWERED``: Risk answered; ``answer`` holds the validated result. A DENIED
  answer describes this request only and proves nothing about other requests.
* ``NOT_SENT``: provably never left this process (route not registered,
  runtime not running, queue full, timeout before start, invalid local input).
* ``UNKNOWN``: anything that cannot be proven either way (timeout after start,
  transport failure after sending, a malformed reply, registration changed
  during the call). The caller keeps the original identity and asks again by
  ``status``; it must not treat UNKNOWN as a refusal.

Answers are claims of the Risk service. They do not authenticate the service,
prove a provider or account fact, or prove that an order was or was not sent.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_base.risk_service_protocol import (
    RISK_ORDER_OPERATIONS,
    RISK_REFUSAL_CODES,
    RISK_SERVICE_SCHEMA,
    RiskOrderResult,
)
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._contracts.models import OrderRequest
from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.gateway_order_contract import decode_gateway_order_request
from bt_api_py.service_config import ClientConfig, parse_client_config
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeRequestNotSentError,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import (
    RuntimeSnapshot,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)

RISK_ORDER_MOUNT = "broker.risk_order"
# risk.parent.* is reserved for an authorized Execution delegate (64.01 section 4).
RISK_PARENT_OPERATIONS = frozenset(
    {"risk.parent.reserve", "risk.parent.release", "risk.parent.status"}
)
STRATEGY_RISK_OPERATIONS = RISK_ORDER_OPERATIONS - RISK_PARENT_OPERATIONS
REQUIRED_STRATEGY_RISK_OPERATIONS = frozenset(
    {"risk.order.reserve", "risk.order.dispatch", "risk.order.release", "risk.order.status"}
)

# Mirrors gateway_direct_client: the threaded runtime proves these never started.
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
_GATEWAY_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
# Outcomes each operation may answer; NOT_FOUND exists only for status.
_ALLOWED_OUTCOMES = MappingProxyType(
    {
        "risk.order.reserve": frozenset(
            {"RESERVED", "DENIED", "UNKNOWN", "DISPATCHED", "RELEASED", "TERMINAL"}
        ),
        "risk.order.dispatch": frozenset(
            {"DISPATCHED", "DENIED", "RELEASED", "UNKNOWN", "TERMINAL"}
        ),
        "risk.order.release": frozenset(
            {"RELEASED", "DENIED", "UNKNOWN", "DISPATCHED", "TERMINAL"}
        ),
        "risk.order.status": frozenset(
            {"RESERVED", "DENIED", "UNKNOWN", "DISPATCHED", "RELEASED", "TERMINAL", "NOT_FOUND"}
        ),
    }
)


def _ref(value: object) -> bool:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 256
    except UnicodeError:
        return False


def _timeout(value: object) -> bool:
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and 0 < value <= threading.TIMEOUT_MAX
    )


@dataclass(frozen=True, slots=True)
class RiskClientRoute:
    """Where this strategy reaches its Risk service; it grants nothing."""

    mount: str
    service_id: str
    instance_id: str
    scope_ref: str
    scope: WireScope
    identity: WireIdentity

    def __post_init__(self) -> None:
        if (
            self.mount != RISK_ORDER_MOUNT
            or not all(_ref(v) for v in (self.service_id, self.instance_id, self.scope_ref))
            or type(self.scope) is not WireScope
            or type(self.identity) is not WireIdentity
            or self.identity.role is not WireRole.STRATEGY_CLIENT
            or self.identity.delegate_ref is not None
            or self.scope.account_ref is None
            or self.scope.strategy_ref is None
            or self.scope.run_id is None
            or self.identity.actor_ref != self.scope.strategy_ref
            or self.scope.account_refs
            or self.scope.portfolio_ref is not None
            or self.scope.manual_book_ref is not None
            or self.scope.group_id is not None
            or self.scope.leg_id is not None
        ):
            raise ValueError("INVALID_RISK_ROUTE")


@dataclass(frozen=True, slots=True)
class RiskCallResult:
    """``ANSWERED`` with a validated answer, or ``NOT_SENT``/``UNKNOWN`` with a code."""

    outcome: str
    answer: RiskOrderResult | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class RiskServiceStatus:
    """``risk.status`` as reported by the service; a claim, not a readiness proof."""

    available: bool
    policy_version: str | None
    refusal_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class RiskStatusResult:
    outcome: str
    status: RiskServiceStatus | None = None
    code: str | None = None


def _not_sent(code: str) -> RiskCallResult:
    return RiskCallResult("NOT_SENT", code=code)


def _unknown(code: str) -> RiskCallResult:
    return RiskCallResult("UNKNOWN", code=code)


# Seconds to wait after the n-th attempt to bring a lost Risk route back.
RECONNECT_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)


class RiskServiceClient:
    """One strategy and one Risk route; a lost runtime is replaced, never resent.

    A runtime handle cannot be restarted. With a ``runtime_factory`` the client
    replaces a runtime that failed or closed with a new one on a later
    :meth:`reconnect`, spaced by :data:`RECONNECT_BACKOFF_SECONDS`. Requests
    whose outcome is unknown stay as they are: they are never repeated, and
    only explicit status reads resolve them.
    """

    def __init__(
        self,
        runtime: ThreadedServiceClientRuntime,
        *,
        route: RiskClientRoute,
        runtime_factory=None,
        monotonic_clock=time.monotonic,
    ) -> None:
        if type(runtime) is not ThreadedServiceClientRuntime or type(route) is not RiskClientRoute:
            raise TypeError("INVALID_RISK_CLIENT")
        if (runtime_factory is not None and not callable(runtime_factory)) or not callable(
            monotonic_clock
        ):
            raise TypeError("INVALID_RISK_CLIENT")
        RiskClientRoute(*(getattr(route, name) for name in RiskClientRoute.__dataclass_fields__))
        self._runtime, self._route = runtime, route
        self._pid = os.getpid()
        self._factory, self._clock = runtime_factory, monotonic_clock
        self._reconnect_attempt, self._reconnect_next = 0, 0.0
        self._reconnect_lock = threading.Lock()

    def reconnect(self, *, timeout_seconds: float = 5.0) -> bool:
        """Bring the Risk route back if possible now; True when it is available.

        A runtime that was never started is started; one that failed or closed
        is closed and replaced by a new runtime from the factory. A running but
        momentarily unhealthy runtime is left to recover by itself. Attempts are
        spaced by the backoff; nothing that was in flight is sent again.
        """
        if os.getpid() != self._pid or not _timeout(timeout_seconds):
            return False
        if not self._reconnect_lock.acquire(blocking=False):
            return False
        try:
            if self.available:
                self._reconnect_attempt, self._reconnect_next = 0, 0.0
                return True
            now = self._clock()
            if now < self._reconnect_next:
                return False
            attempt = self._reconnect_attempt
            self._reconnect_next = now + RECONNECT_BACKOFF_SECONDS[
                min(attempt, len(RECONNECT_BACKOFF_SECONDS) - 1)
            ]
            self._reconnect_attempt = attempt + 1
            snapshot = self._runtime.status()
            if snapshot.state == "new":
                runtime = self._runtime
            elif snapshot.state in ("failed", "closed", "close_failed", "stopping"):
                if self._factory is None:
                    return False
                try:
                    closed = self._runtime.close(timeout_seconds=timeout_seconds)
                except Exception:
                    return False  # resources not released yet: try again later
                if closed.state not in ("closed", "failed") or closed.thread_alive:
                    return False
                runtime = self._factory()
                if type(runtime) is not ThreadedServiceClientRuntime:
                    return False
                self._runtime = runtime
            else:
                return False  # running/starting: it reports health by itself
            try:
                runtime.start(timeout_seconds=timeout_seconds)
            except Exception:
                return False
            if self.available:
                self._reconnect_attempt, self._reconnect_next = 0, 0.0
                return True
            return False
        finally:
            self._reconnect_lock.release()

    @classmethod
    def compose(
        cls,
        config: ClientConfig,
        *,
        route: RiskClientRoute,
        peer: WireIdentity,
        security_profiles: Mapping[str, ClientCurveKeys],
        requested_operations: frozenset[str],
        queue_size: int = 32,
    ) -> RiskServiceClient:
        """Compose an inert client; nothing connects before :meth:`start`."""
        if type(config) is not ClientConfig or type(route) is not RiskClientRoute:
            raise ValueError("INVALID_RISK_CLIENT_CONFIGURATION")
        try:
            config_copy = parse_client_config(config.to_public_dict())
            validate_risk_client_config(config_copy, route, peer, requested_operations)
            profile = next(iter(config_copy.communication["profiles"].values()))
            security_ref = profile["security_profile_ref"]
            if not isinstance(security_profiles, Mapping) or set(security_profiles) != {security_ref}:
                raise ValueError
            keys = security_profiles[security_ref]
            if type(keys) is not ClientCurveKeys or any(
                type(value) is not bytes or len(value) != 40
                for value in (keys.server_key, keys.public_key, keys.secret_key)
            ):
                raise ValueError
            frozen_security = MappingProxyType(
                {security_ref: ClientCurveKeys(keys.server_key, keys.public_key, keys.secret_key)}
            )
            peer_copy = WireIdentity(peer.role, peer.principal_ref, peer.actor_ref, peer.delegate_ref)
        except Exception:
            raise ValueError("INVALID_RISK_CLIENT_CONFIGURATION") from None
        scope = route.scope
        base_scope = WireScope(scope.execution_domain_ref, account_ref=scope.account_ref)
        identity = WireIdentity(
            route.identity.role,
            route.identity.principal_ref,
            route.identity.actor_ref,
            route.identity.delegate_ref,
        )
        operations = {RISK_ORDER_MOUNT: frozenset(requested_operations)}

        def scope_resolver(scope_ref: str) -> WireScope:
            if scope_ref != route.scope_ref:
                raise ValueError("INVALID_SCOPE")
            return base_scope

        def peer_resolver(service_id: str) -> WireIdentity:
            if service_id != route.service_id:
                raise ValueError("INVALID_PEER")
            return peer_copy

        def runtime_factory() -> ServiceClientRuntime:
            return ServiceClientRuntime(
                config_copy,
                identity=identity,
                scope_resolver=scope_resolver,
                peer_resolver=peer_resolver,
                transport_factory=ZmqTransportFactory(frozen_security),
                run_id=scope.run_id,
                requested_operations=operations,
            )

        def new_runtime() -> ThreadedServiceClientRuntime:
            return ThreadedServiceClientRuntime(runtime_factory, queue_size=queue_size)

        return cls(new_runtime(), route=route, runtime_factory=new_runtime)

    @property
    def route(self) -> RiskClientRoute:
        return self._route

    def start(self, *, timeout_seconds: float = 5.0) -> RuntimeSnapshot:
        return self._runtime.start(timeout_seconds=timeout_seconds)

    def close(self, *, timeout_seconds: float = 5.0) -> RuntimeSnapshot:
        return self._runtime.close(timeout_seconds=timeout_seconds)

    def runtime_status(self) -> RuntimeSnapshot:
        return self._runtime.status()

    @property
    def available(self) -> bool:
        """Local view only: the reserve route is registered and healthy right now."""
        try:
            self._registration("risk.order.reserve")
        except Exception:
            return False
        return True

    def _registration(self, operation: str) -> tuple[str, str]:
        if os.getpid() != self._pid:
            raise ValueError("WRONG_PROCESS")
        snapshot = self._runtime.status()
        route = self._route
        matches = [
            b
            for b in snapshot.bindings
            if b.active is True
            and b.required is True
            and b.mount == route.mount
            and b.service_id == route.service_id
            and b.instance_id == route.instance_id
            and b.scope_ref == route.scope_ref
            and type(b.scope) is WireScope
            and b.scope == route.scope
            and type(b.operations) is frozenset
            and operation in b.operations
        ]
        if (
            snapshot.state != "running"
            or not snapshot.thread_alive
            or not snapshot.healthy
            or type(snapshot.identity) is not WireIdentity
            or snapshot.identity != route.identity
            or snapshot.run_id != route.scope.run_id
            or len(matches) != 1
            or not _ref(matches[0].registration_id)
            or not _ref(matches[0].service_epoch)
        ):
            raise ValueError("ROUTE_UNAVAILABLE")
        return matches[0].registration_id, matches[0].service_epoch

    def _call(self, operation: str, arguments: dict[str, Any], timeout_seconds: float):
        """Return ``(raw_reply, None)`` or ``(None, RiskCallResult)``; never resends."""
        if os.getpid() != self._pid:
            return None, _not_sent("WRONG_PROCESS")
        try:
            registration = self._registration(operation)
        except Exception:
            return None, _not_sent("ROUTE_UNAVAILABLE")
        try:
            raw = self._runtime.call(
                self._route.mount,
                operation,
                arguments,
                account_ref=self._route.scope.account_ref,
                scope_ref=self._route.scope_ref,
                timeout_seconds=timeout_seconds,
            )
        except ServiceRuntimeRequestNotSentError:
            return None, _not_sent("REQUEST_NOT_SENT")
        except ThreadedRuntimeError as error:
            if error.code in _NOT_STARTED_ERRORS:
                return None, _not_sent(error.code)
            return None, _unknown("RPC_OUTCOME_UNKNOWN")
        except Exception:
            return None, _unknown("RPC_OUTCOME_UNKNOWN")
        try:
            # A reply that crossed a re-registration cannot be tied to this call.
            if self._registration(operation) != registration:
                raise ValueError
            return _detach_wire(raw), None
        except Exception:
            return None, _unknown("INVALID_REPLY")

    def _answer(self, operation: str, arguments: dict[str, Any], timeout_seconds: float):
        raw, failure = self._call(operation, arguments, timeout_seconds)
        if failure is not None:
            return failure
        try:
            answer = RiskOrderResult.from_mapping(raw)
            key = arguments["request_key"]
            if answer.request_key != key or answer.outcome not in _ALLOWED_OUTCOMES[operation]:
                raise ValueError
            if answer.outcome == "RESERVED" and (
                answer.permit_id is None
                or answer.reservation_id is None
                or answer.view_digest is None
                or answer.code is not None
                or answer.state not in (None, "RESERVED")
            ):
                raise ValueError
            if answer.outcome in ("DISPATCHED", "RELEASED") and answer.code is not None:
                raise ValueError
            if answer.code == "risk_gateway_refused" and (
                type(answer.gateway_code) is not str
                or _GATEWAY_CODE.fullmatch(answer.gateway_code) is None
            ):
                raise ValueError
            if "permit_id" in arguments and answer.permit_id not in (None, arguments["permit_id"]):
                raise ValueError
            if "reservation_id" in arguments and answer.reservation_id not in (
                None,
                arguments["reservation_id"],
            ):
                raise ValueError
            if "view_digest" in arguments and answer.view_digest not in (
                None,
                arguments["view_digest"],
            ):
                raise ValueError
        except Exception:
            return _unknown("INVALID_REPLY")
        return RiskCallResult("ANSWERED", answer)

    def reserve(
        self,
        request: OrderRequest,
        *,
        request_key: str,
        parent_ref: str | None = None,
        child_id: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> RiskCallResult:
        """Ask Risk to check and reserve one order; the canonical payload equals direct.reserve's."""
        try:
            if (
                type(request) is not OrderRequest
                or not _ref(request_key)
                or (parent_ref is None) != (child_id is None)
                or (parent_ref is not None and (not _ref(parent_ref) or not _ref(child_id)))
                or not _timeout(timeout_seconds)
            ):
                raise ValueError
            canonical = canonical_gateway_payload(request.to_dict())
            payload = json.loads(canonical)
            decoded = decode_gateway_order_request(payload, self._route.scope.account_ref)
            # The strategy's request key is the Gateway command key Risk forwards.
            if decoded.idempotency_key != request_key or canonical_gateway_payload(payload) != canonical:
                raise ValueError
        except Exception:
            return _not_sent("INVALID_REQUEST")
        arguments = {
            "request_key": request_key,
            "payload": payload,
            "parent_ref": parent_ref,
            "child_id": child_id,
        }
        return self._answer("risk.order.reserve", arguments, timeout_seconds)

    def dispatch(
        self,
        request_key: str,
        *,
        permit_id: str,
        reservation_id: str,
        view_digest: str,
        market_ref: Mapping[str, Any] | None = None,
        timeout_seconds: float = 5.0,
    ) -> RiskCallResult:
        if (
            not all(_ref(value) for value in (request_key, permit_id, reservation_id))
            or type(view_digest) is not str
            or _DIGEST.fullmatch(view_digest) is None
            or not _timeout(timeout_seconds)
        ):
            return _not_sent("INVALID_REQUEST")
        arguments: dict[str, Any] = {
            "request_key": request_key,
            "permit_id": permit_id,
            "reservation_id": reservation_id,
            "view_digest": view_digest,
        }
        if market_ref is not None:
            try:
                from bt_api_base.market_protocol import MarketSubscriptionKey

                if (
                    not isinstance(market_ref, Mapping)
                    or set(market_ref) != {"key", "epoch", "sequence", "message_id"}
                    or not _ref(market_ref["epoch"])
                    or not _ref(market_ref["message_id"])
                    or type(market_ref["sequence"]) is not int
                    or market_ref["sequence"] <= 0
                ):
                    raise ValueError
                arguments["market_ref"] = {
                    "key": MarketSubscriptionKey.from_dict(market_ref["key"]).to_dict(),
                    "epoch": market_ref["epoch"],
                    "sequence": market_ref["sequence"],
                    "message_id": market_ref["message_id"],
                }
            except Exception:
                return _not_sent("INVALID_REQUEST")
        return self._answer("risk.order.dispatch", arguments, timeout_seconds)

    def release(
        self, request_key: str, *, permit_id: str, timeout_seconds: float = 5.0
    ) -> RiskCallResult:
        if not _ref(request_key) or not _ref(permit_id) or not _timeout(timeout_seconds):
            return _not_sent("INVALID_REQUEST")
        return self._answer(
            "risk.order.release",
            {"request_key": request_key, "permit_id": permit_id},
            timeout_seconds,
        )

    def status(self, request_key: str, *, timeout_seconds: float = 5.0) -> RiskCallResult:
        """NOT_FOUND is an answer about Risk's records, never a proof of not sent."""
        if not _ref(request_key) or not _timeout(timeout_seconds):
            return _not_sent("INVALID_REQUEST")
        return self._answer("risk.order.status", {"request_key": request_key}, timeout_seconds)

    def service_status(self, *, timeout_seconds: float = 5.0) -> RiskStatusResult:
        """Read ``risk.status``; the reply shape is this client's strict expectation.

        Expected reply: ``{"schema": RISK_SERVICE_SCHEMA, "schema_version": 1,
        "available": bool, "policy_version": str | None, "refusal_counts":
        {risk code: int >= 0}}``.
        """
        if not _timeout(timeout_seconds):
            return RiskStatusResult("NOT_SENT", code="INVALID_REQUEST")
        raw, failure = self._call("risk.status", {}, timeout_seconds)
        if failure is not None:
            return RiskStatusResult(failure.outcome, code=failure.code)
        try:
            if (
                type(raw) is not dict
                or set(raw)
                != {"schema", "schema_version", "available", "policy_version", "refusal_counts"}
                or raw["schema"] != RISK_SERVICE_SCHEMA
                or type(raw["schema_version"]) is not int
                or raw["schema_version"] != 1
                or type(raw["available"]) is not bool
                or (raw["policy_version"] is not None and not _ref(raw["policy_version"]))
                or type(raw["refusal_counts"]) is not dict
                or any(
                    code not in RISK_REFUSAL_CODES or type(count) is not int or count < 0
                    for code, count in raw["refusal_counts"].items()
                )
            ):
                raise ValueError
            status = RiskServiceStatus(
                raw["available"],
                raw["policy_version"],
                MappingProxyType(dict(raw["refusal_counts"])),
            )
        except Exception:
            return RiskStatusResult("UNKNOWN", code="INVALID_REPLY")
        return RiskStatusResult("ANSWERED", status)


def validate_risk_client_config(
    config: ClientConfig,
    route: RiskClientRoute,
    peer: WireIdentity,
    requested_operations: frozenset[str],
) -> None:
    """Public cross-checks of one Risk client; raises ValueError without values."""
    if type(config) is not ClientConfig or type(route) is not RiskClientRoute:
        raise ValueError("INVALID_RISK_CLIENT_CONFIGURATION")
    if config.process_role != "strategy_client" or set(config.clients) != {"risk"}:
        raise ValueError("INVALID_RISK_CLIENT_CONFIGURATION")
    bindings = config.clients["risk"]["settings"]["bindings"]
    if len(bindings) != 1:
        raise ValueError("INVALID_RISK_CLIENT_CONFIGURATION")
    selected = bindings[0]
    profiles = config.communication["profiles"]
    if (
        selected["mount"] != RISK_ORDER_MOUNT
        or selected["required"] is not True
        or selected["service_id"] != route.service_id
        or selected["instance_id"] != route.instance_id
        or selected["scope_ref"] != route.scope_ref
        or selected.get("account_ref") != route.scope.account_ref
        or config.registration["strategy_ref"] != route.scope.strategy_ref
        or set(profiles) != {selected["transport_ref"]}
    ):
        raise ValueError("INVALID_RISK_CLIENT_CONFIGURATION")
    profile = profiles[selected["transport_ref"]]
    if profile["backend"] != "zmq" or set(profile["endpoints"]) != {"rpc_connect"}:
        raise ValueError("INVALID_RISK_CLIENT_CONFIGURATION")
    if (
        type(peer) is not WireIdentity
        or peer.role is not WireRole.SERVICE
        or peer.actor_ref != route.service_id
        or peer.delegate_ref is not None
        or len(route.scope.run_id) > 128
    ):
        raise ValueError("INVALID_RISK_CLIENT_CONFIGURATION")
    if (
        type(requested_operations) is not frozenset
        or not REQUIRED_STRATEGY_RISK_OPERATIONS <= requested_operations
        or not requested_operations <= STRATEGY_RISK_OPERATIONS
    ):
        raise ValueError("INVALID_RISK_CLIENT_CONFIGURATION")


__all__ = [
    "REQUIRED_STRATEGY_RISK_OPERATIONS",
    "RISK_ORDER_MOUNT",
    "STRATEGY_RISK_OPERATIONS",
    "RiskCallResult",
    "RiskClientRoute",
    "RiskServiceClient",
    "RiskServiceStatus",
    "RiskStatusResult",
    "validate_risk_client_config",
]
