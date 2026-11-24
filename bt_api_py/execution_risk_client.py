"""Execution-service (`WireRole.SERVICE`) client for Risk parent and v2 child RPCs.

All calls run over an authenticated ZMQ CURVE ``ThreadedServiceClientRuntime``
on ``service.risk_order`` using ``WireRole.SERVICE`` identity. Every network,
timeout, or malformed-reply fault fails closed as ``UNKNOWN`` without retrying
or fabricating authorization.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass
from typing import Any

from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_base.risk_service_protocol import (
    RISK_REFUSAL_CODES,
    RISK_SERVICE_SCHEMA,
    RiskOrderResult,
    RiskOrderView,
    RiskParentView,
)
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.gateway_order_contract import decode_gateway_order_request
from bt_api_py.risk_service_client import RiskParentCallResult
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ServiceRuntimeRequestNotSentError,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime

_PRE_SEND_THREADED_CODES = frozenset({
    "NOT_READY",
    "QUEUE_FULL",
    "TIMEOUT_BEFORE_START",
    "CLOSED_BEFORE_START",
    "INVALID_ARGUMENTS",
    "WRONG_PROCESS",
    "OWNER_THREAD_REENTRY",
})
_REGISTRY_REFUSAL_CODES = frozenset({
    "CAPABILITY_UNAVAILABLE",
    "CLOSED",
    "DENIED",
    "NOT_REGISTERED",
    "POLICY_REQUIREMENT_UNMET",
    "SCOPE_DENIED",
    "STALE_EPOCH",
})

UPSTREAM_RISK_ORDER_MOUNT = "upstream.risk_order"

EXECUTION_RISK_OPERATIONS = frozenset(
    {
        "risk.parent.reserve",
        "risk.parent.release",
        "risk.parent.status",
        "risk.order.reserve.v2",
        "risk.order.dispatch.v2",
        "risk.order.status",
        "risk.status",
    }
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RISK_SECURITY_PROFILE = "execution-risk-curve"


def _ref(value: object) -> bool:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 256
    except UnicodeError:
        return False


def _timeout(value: object) -> bool:
    try:
        return (
            type(value) in (int, float)
            and math.isfinite(value)
            and 0 < value <= threading.TIMEOUT_MAX
        )
    except (OverflowError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class ExecutionRiskClientConfig:
    """Validated config for one Execution -> Risk ZMQ CURVE session."""

    service_id: str
    identity: WireIdentity
    scope: WireScope
    risk_service_id: str
    risk_instance_id: str
    risk_scope_ref: str
    risk_endpoint: str
    risk_peer: WireIdentity
    curve_keys: ClientCurveKeys
    request_timeout_ms: int = 15000

    def __post_init__(self) -> None:
        if (
            not all(
                _ref(v)
                for v in (
                    self.service_id,
                    self.risk_service_id,
                    self.risk_instance_id,
                    self.risk_scope_ref,
                    self.risk_endpoint,
                )
            )
            or type(self.identity) is not WireIdentity
            or self.identity.role is not WireRole.SERVICE
            or self.identity.delegate_ref is not None
            or type(self.scope) is not WireScope
            or self.scope.account_ref is None
            or self.scope.strategy_ref is None
            or self.scope.run_id is None
            or self.scope.account_refs
            or type(self.risk_peer) is not WireIdentity
            or self.risk_peer.role is not WireRole.SERVICE
            or self.risk_peer.actor_ref != self.risk_service_id
            or self.risk_peer.delegate_ref is not None
            or type(self.curve_keys) is not ClientCurveKeys
            or type(self.request_timeout_ms) is not int
            or not 100 <= self.request_timeout_ms <= 60_000
        ):
            raise ValueError("INVALID_EXECUTION_RISK_CLIENT_CONFIG")


def _service_client_manifest(config: ExecutionRiskClientConfig):
    """Build a SERVICE-role ServiceConfig manifest targeting Risk's order port."""
    raw = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": config.service_id,
        "scope_ref": config.risk_scope_ref,
        "transport_ref": "unused-bind",
        "registration_policy_ref": "execution-risk-client-policy-v1",
        "service_requirements_ref": "execution-risk-client-requirements-v1",
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": f"{config.service_id}-risk-client",
                    "bind_ports": ["service.execution_parent"],
                    "options": {"role": "atomic_intake"},
                },
            }
        },
        "clients": {
            "risk": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": UPSTREAM_RISK_ORDER_MOUNT,
                            "service_id": config.risk_service_id,
                            "instance_id": config.risk_instance_id,
                            "scope_ref": config.risk_scope_ref,
                            "account_ref": config.scope.account_ref,
                            "transport_ref": "risk-client",
                            "required": True,
                        }
                    ],
                    "options": {"request_timeout_ms": config.request_timeout_ms},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "unused-bind": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:1"},
                    "security_profile_ref": "unused-bind-sec",
                },
                "risk-client": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": config.risk_endpoint},
                    "security_profile_ref": _RISK_SECURITY_PROFILE,
                },
            },
        },
    }
    return parse_service_config(raw)


class ExecutionRiskServiceClient:
    """Real ZMQ CURVE Execution -> Risk client compatible with ``RiskParentPort``."""

    def __init__(self, config: ExecutionRiskClientConfig) -> None:
        if type(config) is not ExecutionRiskClientConfig:
            raise ValueError("INVALID_EXECUTION_RISK_CLIENT_CONFIG")
        self._config = config
        self._pid = os.getpid()
        manifest = _service_client_manifest(config)
        factory = ZmqTransportFactory({_RISK_SECURITY_PROFILE: config.curve_keys})
        requested = {UPSTREAM_RISK_ORDER_MOUNT: EXECUTION_RISK_OPERATIONS}
        self._runtime = ThreadedServiceClientRuntime(
            lambda: ServiceClientRuntime(
                manifest,
                identity=config.identity,
                scope_resolver=lambda _ref: config.scope,
                peer_resolver=lambda _service: config.risk_peer,
                transport_factory=factory,
                run_id=config.scope.run_id,
                requested_operations=requested,
                require_unregister_ack=True,
            ),
            require_unregister_ack=True,
        )
        self._started = False
        self._closed = False

    def start(self, *, timeout_seconds: float = 10.0) -> None:
        if os.getpid() != self._pid or self._closed:
            raise RuntimeError("EXECUTION_RISK_CLIENT_INVALID_STATE")
        if self._started:
            return
        self._runtime.start(timeout_seconds=timeout_seconds)
        self._started = True

    def close(self, *, timeout_seconds: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self._started = False
        try:
            self._runtime.close(timeout_seconds=timeout_seconds)
        except Exception:
            pass

    def _registration(self, operation: str) -> tuple[str, str]:
        if os.getpid() != self._pid or not self._started or self._closed:
            raise ValueError("ROUTE_UNAVAILABLE")
        snapshot = self._runtime.status()
        cfg = self._config
        matches = [
            b
            for b in snapshot.bindings
            if b.active is True
            and b.mount == UPSTREAM_RISK_ORDER_MOUNT
            and b.service_id == cfg.risk_service_id
            and b.instance_id == cfg.risk_instance_id
            and b.scope_ref == cfg.risk_scope_ref
            and b.scope == cfg.scope
            and operation in b.operations
        ]
        if (
            snapshot.state != "running"
            or not snapshot.thread_alive
            or not snapshot.healthy
            or snapshot.identity != cfg.identity
            or snapshot.run_id != cfg.scope.run_id
            or len(matches) != 1
            or not _ref(matches[0].registration_id)
            or not _ref(matches[0].service_epoch)
        ):
            raise ValueError("ROUTE_UNAVAILABLE")
        return matches[0].registration_id, matches[0].service_epoch

    def available(self) -> bool:
        try:
            for op in ("risk.parent.reserve", "risk.order.reserve.v2", "risk.order.dispatch.v2"):
                self._registration(op)
        except Exception:
            return False
        return True

    def _rpc(
        self, operation: str, arguments: dict[str, Any], timeout_seconds: float
    ) -> tuple[dict[str, Any] | None, str | None, bool]:
        try:
            reg_id, epoch = self._registration(operation)
        except Exception:
            return None, "ROUTE_UNAVAILABLE", True
        try:
            raw = self._runtime.call(
                UPSTREAM_RISK_ORDER_MOUNT,
                operation,
                arguments,
                account_ref=self._config.scope.account_ref,
                scope_ref=self._config.risk_scope_ref,
                timeout_seconds=timeout_seconds,
            )
            detached = _detach_wire(raw)
            if type(detached) is not dict:
                return None, "INVALID_REPLY", False
            try:
                if self._registration(operation) != (reg_id, epoch):
                    return None, "INVALID_REPLY", False
            except Exception:
                return None, "INVALID_REPLY", False
            return detached, None, False
        except ServiceRuntimeRequestNotSentError as exc:
            return None, getattr(exc, "code", None) or "NOT_SENT", True
        except ThreadedRuntimeError as exc:
            code = getattr(exc, "code", None) or "UNKNOWN"
            return None, code, code in _PRE_SEND_THREADED_CODES
        except ServiceRuntimeError as exc:
            reply_code = getattr(exc, "service_reply_code", None)
            err_code = reply_code or getattr(exc, "code", None) or "UNKNOWN"
            if (
                type(reply_code) is str
                and reply_code == getattr(exc, "code", None)
                and reply_code in _REGISTRY_REFUSAL_CODES
            ):
                try:
                    unchanged = self._registration(operation) == (reg_id, epoch)
                except Exception:
                    unchanged = False
                if unchanged:
                    return None, reply_code, True
            return None, err_code, False
        except Exception:
            return None, "UNKNOWN", False

    def _parse_parent_reply(
        self, raw: dict[str, Any] | None, err: str | None, *, not_sent: bool = False
    ) -> RiskParentCallResult:
        if raw is None:
            return RiskParentCallResult("NOT_SENT" if not_sent else "UNKNOWN", code=err)
        try:
            if (
                raw.get("schema") != RISK_SERVICE_SCHEMA
                or raw.get("schema_version") != 1
                or raw.get("outcome") not in {"RESERVED", "RELEASED", "DENIED", "UNKNOWN", "NOT_FOUND"}
            ):
                return RiskParentCallResult("UNKNOWN", code="INVALID_REPLY")
            parent_ref = raw.get("parent_ref")
            if raw.get("outcome") != "DENIED" and not _ref(parent_ref):
                return RiskParentCallResult("UNKNOWN", code="INVALID_REPLY")
            code = raw.get("code")
            if code is not None and code not in RISK_REFUSAL_CODES:
                return RiskParentCallResult("UNKNOWN", code="INVALID_REPLY")
            return RiskParentCallResult(
                outcome="ANSWERED",
                parent_ref=parent_ref if _ref(parent_ref) else None,
                parent_outcome=raw["outcome"],
                state=raw.get("state"),
                code=code,
                total_quantity=raw.get("total_quantity"),
                allocated_quantity=raw.get("allocated_quantity"),
                unallocated_quantity=raw.get("unallocated_quantity"),
                policy_version=raw.get("policy_version"),
            )
        except Exception:
            return RiskParentCallResult("UNKNOWN", code="INVALID_REPLY")

    def parent_reserve(
        self, parent: RiskParentView, *, timeout_seconds: float = 15.0
    ) -> RiskParentCallResult:
        if type(parent) is not RiskParentView or not _timeout(timeout_seconds):
            return RiskParentCallResult("NOT_SENT", code="INVALID_REQUEST")
        raw, err, not_sent = self._rpc("risk.parent.reserve", {"parent": parent.to_mapping()}, timeout_seconds)
        return self._parse_parent_reply(raw, err, not_sent=not_sent)

    def parent_release(
        self, parent_ref: str, *, timeout_seconds: float = 15.0
    ) -> RiskParentCallResult:
        if not _ref(parent_ref) or not _timeout(timeout_seconds):
            return RiskParentCallResult("NOT_SENT", code="INVALID_REQUEST")
        raw, err, not_sent = self._rpc("risk.parent.release", {"parent_ref": parent_ref}, timeout_seconds)
        return self._parse_parent_reply(raw, err, not_sent=not_sent)

    def parent_status(
        self, parent_ref: str, *, timeout_seconds: float = 15.0
    ) -> RiskParentCallResult:
        if not _ref(parent_ref) or not _timeout(timeout_seconds):
            return RiskParentCallResult("NOT_SENT", code="INVALID_REQUEST")
        raw, err, not_sent = self._rpc("risk.parent.status", {"parent_ref": parent_ref}, timeout_seconds)
        return self._parse_parent_reply(raw, err, not_sent=not_sent)

    def order_status(
        self, request_key: str, *, timeout_seconds: float = 15.0
    ) -> RiskOrderResult:
        if not _ref(request_key) or not _timeout(timeout_seconds):
            return RiskOrderResult("unknown", "UNKNOWN")
        raw, err, _not_sent = self._rpc("risk.order.status", {"request_key": request_key}, timeout_seconds)
        if raw is None:
            return RiskOrderResult(
                request_key, "UNKNOWN", code=err if err in RISK_REFUSAL_CODES else None
            )
        try:
            parsed = RiskOrderResult.from_mapping(raw)
            if parsed.request_key != request_key:
                return RiskOrderResult(request_key, "UNKNOWN")
            return parsed
        except Exception:
            return RiskOrderResult(request_key, "UNKNOWN")

    def child_reserve(
        self,
        parent_ref: str,
        child_view: RiskOrderView,
        canonical_payload: str,
        run_id: str | None,
        *,
        timeout_seconds: float = 15.0,
    ) -> RiskOrderResult:
        key = child_view.request_key if isinstance(child_view, RiskOrderView) else "unknown"
        try:
            if (
                not _ref(parent_ref)
                or type(child_view) is not RiskOrderView
                or child_view.origin != "execution"
                or child_view.parent_ref != parent_ref
                or not _ref(child_view.child_id)
                or type(canonical_payload) is not str
                or not canonical_payload
                or not _timeout(timeout_seconds)
                or (run_id is not None and run_id != self._config.scope.run_id)
            ):
                return RiskOrderResult(key, "DENIED", code="risk_request_invalid")
            payload = json.loads(canonical_payload)
            decoded = decode_gateway_order_request(payload, self._config.scope.account_ref)
            if (
                decoded.idempotency_key != child_view.request_key
                or canonical_gateway_payload(payload) != canonical_payload
            ):
                return RiskOrderResult(key, "DENIED", code="risk_request_invalid")
        except Exception:
            return RiskOrderResult(key, "DENIED", code="risk_request_invalid")

        raw, err, not_sent = self._rpc(
            "risk.order.reserve.v2",
            {
                "request_key": child_view.request_key,
                "payload": payload,
                "parent_ref": parent_ref,
                "child_id": child_view.child_id,
            },
            timeout_seconds,
        )
        if raw is None:
            if not_sent:
                return RiskOrderResult(
                    key, "DENIED", code=err if err in RISK_REFUSAL_CODES else None
                )
            return RiskOrderResult(
                key, "UNKNOWN", code=err if err in RISK_REFUSAL_CODES else None
            )
        try:
            parsed = RiskOrderResult.from_mapping(raw)
            if parsed.request_key != key:
                return RiskOrderResult(key, "UNKNOWN")
            return parsed
        except Exception:
            return RiskOrderResult(key, "UNKNOWN")

    def child_dispatch(
        self,
        parent_ref: str,
        request_key: str,
        permit_id: str,
        reservation_id: str,
        view_digest: str,
        *,
        timeout_seconds: float = 15.0,
    ) -> RiskOrderResult:
        if (
            not all(_ref(v) for v in (parent_ref, request_key, permit_id, reservation_id))
            or type(view_digest) is not str
            or _DIGEST.fullmatch(view_digest) is None
            or not _timeout(timeout_seconds)
        ):
            return RiskOrderResult(
                request_key if _ref(request_key) else "unknown",
                "DENIED",
                code="risk_request_invalid",
            )
        raw, err, _not_sent = self._rpc(
            "risk.order.dispatch.v2",
            {
                "request_key": request_key,
                "permit_id": permit_id,
                "reservation_id": reservation_id,
                "view_digest": view_digest,
            },
            timeout_seconds,
        )
        if raw is None:
            return RiskOrderResult(
                request_key, "UNKNOWN", code=err if err in RISK_REFUSAL_CODES else None
            )
        try:
            parsed = RiskOrderResult.from_mapping(raw)
            if parsed.request_key != request_key:
                return RiskOrderResult(request_key, "UNKNOWN")
            return parsed
        except Exception:
            return RiskOrderResult(request_key, "UNKNOWN")


__all__ = [
    "EXECUTION_RISK_OPERATIONS",
    "ExecutionRiskClientConfig",
    "ExecutionRiskServiceClient",
]
