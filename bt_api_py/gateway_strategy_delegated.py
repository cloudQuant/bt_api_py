"""Strategy-scoped observation of commands submitted by a delegated actor."""
from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass

from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.gateway_strategy_position import GatewayStrategyPositionClient, _text
from bt_api_py.gateway_delegated_cancel import (GatewayDelegatedCancelObservation,
    GatewayDelegatedCancelPlan, GatewayDelegatedCancelResult)
from bt_api_py.service_runtime import ServiceRuntimeRequestNotSentError


@dataclass(frozen=True, slots=True)
class GatewayDelegatedCommandObservation:
    account_scope: str
    strategy_scope: str
    actor_principal: str
    request_key: str
    reservation_id: str
    digest: str
    state: str
    version: int
    owner_generation: str
    reservation_generation: str


@dataclass(frozen=True, slots=True)
class GatewayDelegatedCommandResult:
    outcome: str
    observation: GatewayDelegatedCommandObservation | None = None
    code: str | None = None


class GatewayStrategyDelegatedClient(GatewayStrategyPositionClient):
    """Read an explicitly granted command, retaining its original actor."""

    def __init__(self, runtime, *, binding, route, cancel_host=None):
        super().__init__(runtime, binding=binding, route=route)
        names = ("save_delegated_cancel_plan", "load_delegated_cancel_plan",
                 "save_delegated_cancel_observation", "load_delegated_cancel_observation")
        if cancel_host is not None and any(not callable(getattr(cancel_host, name, None)) for name in names):
            raise ValueError("INVALID_DELEGATED_CANCEL_HOST")
        self._cancel_host = cancel_host
        self._observations = {}

    def status(self, request_key: str, *, timeout_seconds=5.0):
        self._check_owner()
        if (not _text(request_key) or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError("GATEWAY_DELEGATED_INVALID_REQUEST")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("GATEWAY_DELEGATED_BUSY")
        try:
            self._observations.pop(request_key, None)
            operation = "direct.delegated_command_status"
            try:
                registration = self._registration(operation)
                response = _detach_wire(self._runtime.call(
                    self._route.mount, operation, {"request_key": request_key},
                    account_ref=self._binding.account_scope, scope_ref=self._route.scope_ref,
                    timeout_seconds=timeout_seconds,
                ))
                if self._registration(operation) != registration or type(response) is not dict:
                    raise ValueError("INVALID_REPLY")
                if response.get("status") == "REQUEST_REJECTED":
                    if response != {"status": "REQUEST_REJECTED", "code": "DELEGATED_COMMAND_UNAVAILABLE"}:
                        raise ValueError("INVALID_REPLY")
                    return GatewayDelegatedCommandResult("UNAVAILABLE", code="DELEGATED_COMMAND_UNAVAILABLE")
                fields = {"status", "schema_version", "account_scope", "strategy_scope", "actor_principal",
                          "request_key", "reservation_id", "digest", "state", "version", "owner_generation",
                          "reservation_generation"}
                if (set(response) != fields or response["status"] != "OBSERVED"
                        or type(response["schema_version"]) is not int or response["schema_version"] != 1
                        or response["account_scope"] != self._binding.account_scope
                        or response["strategy_scope"] != self._binding.strategy_scope
                        or response["request_key"] != request_key
                        or any(not _text(response[k]) for k in ("actor_principal", "reservation_id",
                                                               "owner_generation", "reservation_generation"))
                        or type(response["digest"]) is not str
                        or re.fullmatch(r"[0-9a-f]{64}", response["digest"], re.ASCII) is None
                        or type(response["state"]) is not str
                        or type(response["version"]) is not int
                        or {"RESERVED": 1, "DISPATCHING": 2, "SENT": 3, "NOT_SENT": 3,
                            "UNKNOWN": 3, "RELEASED": 2, "EXPIRED": 2}.get(response["state"]) != response["version"]):
                    raise ValueError("INVALID_REPLY")
                observation = GatewayDelegatedCommandObservation(**{k: response[k] for k in fields - {"status", "schema_version"}})
                self._observations[request_key] = observation
                return GatewayDelegatedCommandResult("OBSERVED", observation)
            except Exception:
                return GatewayDelegatedCommandResult("UNAVAILABLE", code="INVALID_OR_UNAVAILABLE_REPLY")
        finally:
            self._lock.release()

    def _cancel_rpc(self, operation, arguments, plan, timeout_seconds):
        try:
            registration = self._registration(operation)
        except Exception:
            return GatewayDelegatedCancelResult("TRANSPORT_NOT_STARTED", code="ROUTE_UNAVAILABLE")
        try:
            raw = _detach_wire(self._runtime.call(self._route.mount, operation, arguments,
                account_ref=self._binding.account_scope, scope_ref=self._route.scope_ref,
                timeout_seconds=timeout_seconds))
        except ServiceRuntimeRequestNotSentError:
            return GatewayDelegatedCancelResult("TRANSPORT_NOT_STARTED", code="REQUEST_NOT_SENT")
        except Exception:
            return GatewayDelegatedCancelResult("UNKNOWN", code="RPC_OUTCOME_UNKNOWN")
        try:
            if self._registration(operation) != registration or type(raw) is not dict:
                raise ValueError
            if raw.get("status") == "REQUEST_REJECTED":
                if set(raw) != {"status", "code"} or type(raw["code"]) is not str:
                    raise ValueError
                return GatewayDelegatedCancelResult("REQUEST_DENIED", code=raw["code"])
            allowed = {"status", "record"} | ({"cancel_queue_state"} if operation == "direct.delegated_cancel" else set())
            if set(raw) != allowed or raw["status"] != "OK":
                raise ValueError
            if operation == "direct.delegated_cancel" and raw["cancel_queue_state"] not in {"QUEUED", "ALREADY_QUEUED", "OBSERVED"}:
                raise ValueError
            data = raw["record"]
            if type(data) is not dict or set(data) != set(GatewayDelegatedCancelObservation.__dataclass_fields__):
                raise ValueError
            result = GatewayDelegatedCancelObservation(**data)
            if (result.account_scope != self._binding.account_scope or result.strategy_scope != self._binding.strategy_scope
                    or result.requester_principal != self._binding.principal
                    or result.target_actor_principal != plan.target_actor_principal
                    or result.request_key != plan.request_key or result.operation_id != plan.operation_id
                    or result.target_reservation_id != plan.reservation_id or result.target_digest != plan.digest
                    or any(not _text(getattr(result, field)) for field in ("attempt_id", "owner_generation", "attempt_generation"))
                    or type(result.state) is not str or type(result.version) is not int
                    or {"REQUESTED": 1, "DISPATCHING": 2, "SENT": 3, "NOT_SENT": 3, "UNKNOWN": 3}.get(result.state) != result.version):
                raise ValueError
            prior = self._cancel_host.load_delegated_cancel_observation(plan.operation_id)
            if prior is not None and (type(prior) is not GatewayDelegatedCancelObservation
                    or prior.attempt_id != result.attempt_id or prior.version > result.version
                    or any(getattr(prior, name) != getattr(result, name) for name in (
                        "account_scope", "strategy_scope", "requester_principal", "target_actor_principal",
                        "request_key", "operation_id", "target_reservation_id", "target_digest", "attempt_generation"))
                    or (prior.version == result.version and prior.state != result.state)):
                raise ValueError
            saved = self._cancel_host.save_delegated_cancel_observation(plan.operation_id, result)
            if saved != result or self._cancel_host.load_delegated_cancel_observation(plan.operation_id) != result:
                raise ValueError
            queue = raw.get("cancel_queue_state")
            return GatewayDelegatedCancelResult("QUEUED" if queue in {"QUEUED", "ALREADY_QUEUED"}
                                                and result.state == "REQUESTED" else "OBSERVED", result)
        except Exception:
            return GatewayDelegatedCancelResult("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")

    def cancel(self, observation, *, operation_id, timeout_seconds=5.0):
        """Persist an operation once; an existing plan is queried, never resent."""
        self._check_owner()
        if (type(observation) is not GatewayDelegatedCommandObservation or not _text(operation_id)
                or len(operation_id) > 256 or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError("GATEWAY_DELEGATED_INVALID_REQUEST")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("GATEWAY_DELEGATED_BUSY")
        try:
            if self._cancel_host is None:
                return GatewayDelegatedCancelResult("TRANSPORT_NOT_STARTED", code="CANCEL_HOST_UNAVAILABLE")
            try:
                if (self._observations.get(observation.request_key) is not observation
                        or observation.state not in {"DISPATCHING", "SENT", "UNKNOWN"}):
                    raise ValueError
                plan = GatewayDelegatedCancelPlan(self._binding, self._route, operation_id,
                    observation.request_key, observation.reservation_id, observation.digest, observation.actor_principal)
                existing = self._cancel_host.load_delegated_cancel_plan(operation_id)
                if existing is not None:
                    if existing != plan:
                        raise ValueError
                    return self._cancel_rpc("direct.delegated_cancel_status", {"operation_id": operation_id}, plan, timeout_seconds)
                saved = self._cancel_host.save_delegated_cancel_plan(plan)
                if saved != plan or self._cancel_host.load_delegated_cancel_plan(operation_id) != plan:
                    raise ValueError
            except Exception:
                return GatewayDelegatedCancelResult("TRANSPORT_NOT_STARTED", code="CANCEL_NOT_DURABLE")
            return self._cancel_rpc("direct.delegated_cancel", {"request_key": plan.request_key,
                "reservation_id": plan.reservation_id, "digest": plan.digest,
                "operation_id": operation_id}, plan, timeout_seconds)
        finally:
            self._lock.release()

    def cancel_status(self, operation_id, *, timeout_seconds=5.0):
        self._check_owner()
        if (not _text(operation_id) or len(operation_id) > 256 or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError("GATEWAY_DELEGATED_INVALID_REQUEST")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("GATEWAY_DELEGATED_BUSY")
        try:
            if self._cancel_host is None:
                return GatewayDelegatedCancelResult("TRANSPORT_NOT_STARTED", code="CANCEL_HOST_UNAVAILABLE")
            try:
                plan = self._cancel_host.load_delegated_cancel_plan(operation_id)
                if (type(plan) is not GatewayDelegatedCancelPlan or plan.operation_id != operation_id
                        or plan.binding != self._binding or plan.route != self._route):
                    raise ValueError
            except Exception:
                return GatewayDelegatedCancelResult("TRANSPORT_NOT_STARTED", code="CANCEL_PLAN_UNAVAILABLE")
            return self._cancel_rpc("direct.delegated_cancel_status", {"operation_id": operation_id}, plan, timeout_seconds)
        finally:
            self._lock.release()
