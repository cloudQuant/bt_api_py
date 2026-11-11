"""Explicit service client for durable Risk/Execution Gateway delegation.

The host owns durable storage. No client-side declaration grants authority;
the Gateway verifies the authenticated Registry identity and static grant.
"""
from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass
from typing import Protocol

from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import canonical_gateway_payload, gateway_payload_digest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py._contracts import OrderRequest
from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.gateway_order_contract import decode_gateway_order_request
from bt_api_py.gateway_delegated_cancel import GatewayDelegatedCancelObservation
from bt_api_py.service_runtime import ServiceRuntimeError, ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime

# Threaded-runtime errors raised before the call was handed to the transport: the request
# provably never left this process. The same set as the strategy client's
# (``gateway_direct_client._NOT_STARTED_ERRORS``).
RUNTIME_NOT_STARTED_CODES = frozenset({
    "NOT_READY", "QUEUE_FULL", "TIMEOUT_BEFORE_START", "CLOSED_BEFORE_START", "WRONG_PROCESS",
    "OWNER_THREAD_REENTRY", "INVALID_ARGUMENTS"})
# The Registry's own refusal replies (``ServiceRuntimeError.service_reply_code``) that it
# gives before the Gateway handler of these operations runs, or after that handler itself
# refused: for them the refusal proves that the Gateway stored nothing. A delegate's cancel
# and release are rechecked by the Registry after the Gateway ran them, so a refusal there
# proves nothing and stays unknown.
_REGISTRY_REFUSALS = frozenset({
    "DENIED", "SCOPE_DENIED", "NOT_REGISTERED", "STALE_EPOCH", "CAPABILITY_UNAVAILABLE",
    "POLICY_REQUIREMENT_UNMET", "STRATEGY_STOPPED", "DUPLICATE_ACTIVE_RUN", "MIGRATION_WRITE_DISABLED",
    "UNAVAILABLE", "CLOSED", "INVALID_REQUEST", "UNKNOWN_OPERATION"})
_REGISTRY_REFUSED_OPERATIONS = frozenset({
    "direct.delegated_reserve", "direct.delegated_dispatch", "direct.delegate_actor_status",
    "direct.delegate_actor_cancel_status", "direct.delegate_actor_release_status"})


def runtime_failure(error, operation=None):
    """``(outcome, code)`` for an exception from ``runtime.call`` (G64-08).

    ``TRANSPORT_NOT_STARTED`` only when the request provably never left; ``REQUEST_DENIED``
    only for a Registry refusal that proves no effect for ``operation``; otherwise unknown.
    """
    if isinstance(error, ServiceRuntimeRequestNotSentError):
        return "TRANSPORT_NOT_STARTED", "REQUEST_NOT_SENT"
    if isinstance(error, ThreadedRuntimeError) and error.code in RUNTIME_NOT_STARTED_CODES:
        return "TRANSPORT_NOT_STARTED", error.code
    reply = getattr(error, "service_reply_code", None) if isinstance(error, ServiceRuntimeError) else None
    if operation in _REGISTRY_REFUSED_OPERATIONS and reply in _REGISTRY_REFUSALS and reply == error.code:
        return "REQUEST_DENIED", reply
    return "UNKNOWN", "RPC_OUTCOME_UNKNOWN"


@dataclass(frozen=True, slots=True)
class GatewayDelegateRoute:
    mount: str
    service_id: str
    instance_id: str
    scope_ref: str
    scope: WireScope
    identity: WireIdentity

    def __post_init__(self):
        if (self.mount != "upstream.direct_write" or any(type(v) is not str or not v or v != v.strip()
                or len(v) > 256 for v in (self.service_id, self.instance_id, self.scope_ref))
                or type(self.identity) is not WireIdentity or self.identity.role is not WireRole.SERVICE
                or self.identity.delegate_ref is not None or type(self.scope) is not WireScope
                or self.scope.account_ref is None or self.scope.strategy_ref is None or self.scope.run_id is None
                or self.scope.account_refs or self.scope.manual_book_ref is not None
                or self.scope.portfolio_ref is not None or self.scope.group_id is not None or self.scope.leg_id is not None):
            raise ValueError("INVALID_DELEGATE_ROUTE")


@dataclass(frozen=True, slots=True)
class GatewayDelegateIntent:
    route: GatewayDelegateRoute
    command: GatewayDelegatedCommand
    canonical_payload: str
    ttl_seconds: float


@dataclass(frozen=True, slots=True)
class GatewayDelegateObservation:
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
class GatewayDelegateResult:
    outcome: str
    observation: GatewayDelegateObservation | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class GatewayDelegateActorObservation:
    command: GatewayDelegateObservation
    native_identity_digest: str | None


@dataclass(frozen=True, slots=True)
class GatewayDelegateActorCancelIntent:
    request_key: str
    operation_id: str
    command_digest: str
    reservation_id: str
    payload_digest: str
    actor_principal: str
    native_identity_digest: str


@dataclass(frozen=True, slots=True)
class GatewayDelegateActorReleaseIntent:
    request_key: str
    operation_id: str
    command_digest: str
    reservation_id: str
    payload_digest: str
    actor_principal: str
    expected_version: int


@dataclass(frozen=True, slots=True)
class GatewayDelegateActorReleaseResult:
    outcome: str
    state: str | None = None
    reservation_id: str | None = None
    operation_id: str | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class GatewayDelegateActorResult:
    outcome: str
    observation: GatewayDelegateActorObservation | None = None
    cancel: GatewayDelegatedCancelObservation | None = None
    code: str | None = None


class GatewayDelegateHost(Protocol):
    def save_delegate_intent(self, intent: GatewayDelegateIntent) -> GatewayDelegateIntent: ...
    def load_delegate_intent(self, request_key: str) -> GatewayDelegateIntent | None: ...
    def save_delegate_observation(self, request_key: str, observation: GatewayDelegateObservation) -> GatewayDelegateObservation: ...
    def load_delegate_observation(self, request_key: str) -> GatewayDelegateObservation | None: ...
    def save_delegate_dispatch_marker(self, request_key: str, marker: tuple) -> tuple: ...
    def load_delegate_dispatch_marker(self, request_key: str) -> tuple | None: ...


class GatewayDelegateServiceClient:
    """One service principal and fixed target strategy; no automatic resend."""
    def __init__(self, runtime: ThreadedServiceClientRuntime, *, route: GatewayDelegateRoute, host: GatewayDelegateHost):
        names = ("save_delegate_intent", "load_delegate_intent", "save_delegate_observation", "load_delegate_observation",
                 "save_delegate_dispatch_marker", "load_delegate_dispatch_marker")
        if (type(runtime) is not ThreadedServiceClientRuntime or type(route) is not GatewayDelegateRoute
                or any(not callable(getattr(host, name, None)) for name in names)):
            raise ValueError("INVALID_DELEGATE_CLIENT")
        self._runtime, self._route, self._host = runtime, route, host
        self._pid, self._thread, self._lock = os.getpid(), threading.current_thread(), threading.Lock()
        self._actor_observations = {}
        # request_key -> dispatch marker of an attempt this client proved never left
        # (TRANSPORT_NOT_STARTED). In memory only: a restarted host cannot tell that its
        # predecessor's attempt never left, so its durable marker stays unresolved.
        self._unsent_dispatch = {}

    def _owned(self):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError("DELEGATE_WRONG_OWNER")

    def _registration(self, operation):
        snap = self._runtime.status()
        route = self._route
        matches = [b for b in snap.bindings if b.active is True and b.required is True
                   and b.mount == route.mount and b.service_id == route.service_id
                   and b.instance_id == route.instance_id and b.scope_ref == route.scope_ref
                   and type(b.scope) is WireScope and b.scope == route.scope
                   and type(b.operations) is frozenset and operation in b.operations]
        if (snap.state != "running" or not snap.thread_alive or not snap.healthy
                or snap.identity != route.identity or snap.run_id != route.scope.run_id or len(matches) != 1
                or type(matches[0].registration_id) is not str or not matches[0].registration_id
                or type(matches[0].service_epoch) is not str or not matches[0].service_epoch):
            raise ValueError("ROUTE_UNAVAILABLE")
        return matches[0].registration_id, matches[0].service_epoch

    def _validate(self, intent):
        if type(intent) is not GatewayDelegateIntent or intent.route != self._route:
            raise ValueError("INVALID_INTENT")
        cmd, route = intent.command, self._route
        if (type(cmd) is not GatewayDelegatedCommand or cmd.actor_principal != route.identity.principal_ref
                or cmd.actor_ref != route.identity.actor_ref or cmd.account_scope != route.scope.account_ref
                or cmd.strategy_scope != route.scope.strategy_ref or cmd.execution_domain_ref != route.scope.execution_domain_ref
                or cmd.run_id != route.scope.run_id or type(intent.ttl_seconds) not in (int, float)
                or not math.isfinite(intent.ttl_seconds) or not 0 < intent.ttl_seconds <= 30):
            raise ValueError("INVALID_INTENT")
        payload = json.loads(intent.canonical_payload)
        decoded = decode_gateway_order_request(payload, cmd.account_scope)
        if (canonical_gateway_payload(payload) != intent.canonical_payload
                or gateway_payload_digest(payload) != cmd.payload_digest
                or decoded.idempotency_key != cmd.request_key or decoded.symbol != cmd.instrument.native_symbol
                or decoded.exchange_id != cmd.instrument.venue):
            raise ValueError("INVALID_INTENT")
        return payload

    def _rpc(self, operation, arguments, intent, timeout_seconds):
        try:
            registration = self._registration(operation)
        except Exception:
            return GatewayDelegateResult("TRANSPORT_NOT_STARTED", code="ROUTE_UNAVAILABLE")
        try:
            raw = _detach_wire(self._runtime.call(self._route.mount, operation, arguments,
                account_ref=intent.command.account_scope, scope_ref=self._route.scope_ref, timeout_seconds=timeout_seconds))
        except Exception as error:
            outcome, code = runtime_failure(error, operation)
            return GatewayDelegateResult(outcome, code=code)
        try:
            if self._registration(operation) != registration or type(raw) is not dict:
                raise ValueError
            if raw == {"status": "UNKNOWN"}:
                return GatewayDelegateResult("UNKNOWN")
            if raw.get("status") == "REQUEST_REJECTED":
                if set(raw) != {"status", "code"} or type(raw["code"]) is not str:
                    raise ValueError
                return GatewayDelegateResult("REQUEST_DENIED", code=raw["code"])
            if raw.get("status") in ("QUEUED", "ALREADY_QUEUED"):
                if (operation != "direct.delegated_dispatch" or set(raw) != {"status", "request_key", "reservation_id"}
                        or raw["request_key"] != intent.command.request_key
                        or raw["reservation_id"] != arguments["reservation_id"]):
                    raise ValueError
                return GatewayDelegateResult("QUEUED")
            fields = set(GatewayDelegateObservation.__dataclass_fields__)
            if set(raw) != fields | {"status", "schema_version"} or raw["status"] != "OBSERVED" or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
                raise ValueError
            obs = GatewayDelegateObservation(**{name: raw[name] for name in fields})
            cmd = intent.command
            if (obs.account_scope != cmd.account_scope or obs.strategy_scope != cmd.strategy_scope
                    or obs.actor_principal != cmd.actor_principal or obs.request_key != cmd.request_key
                    or obs.digest != cmd.payload_digest or type(obs.version) is not int or type(obs.state) is not str
                    or {"RESERVED": 1, "DISPATCHING": 2, "SENT": 3, "NOT_SENT": 3, "UNKNOWN": 3,
                        "RELEASED": 2, "EXPIRED": 2}.get(obs.state) != obs.version
                    or any(type(v) is not str or not v or v != v.strip() or len(v) > 256
                           for v in (obs.reservation_id, obs.owner_generation, obs.reservation_generation))):
                raise ValueError
            old = self._host.load_delegate_observation(cmd.request_key)
            if old is not None and (type(old) is not GatewayDelegateObservation or old.reservation_id != obs.reservation_id
                    or old.digest != obs.digest or old.actor_principal != obs.actor_principal or old.version > obs.version
                    or (old.version == obs.version and old.state != obs.state)):
                raise ValueError
            if (self._host.save_delegate_observation(cmd.request_key, obs) != obs
                    or self._host.load_delegate_observation(cmd.request_key) != obs):
                raise ValueError
            return GatewayDelegateResult("OBSERVED", obs)
        except Exception:
            return GatewayDelegateResult("UNKNOWN", code="INVALID_OR_UNAVAILABLE_REPLY")

    def reserve(self, command: GatewayDelegatedCommand, request: OrderRequest, *, ttl_seconds=10.0, timeout_seconds=5.0):
        self._owned()
        if type(request) is not OrderRequest or type(command) is not GatewayDelegatedCommand:
            raise ValueError("INVALID_DELEGATE_REQUEST")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX:
            raise ValueError("INVALID_TIMEOUT")
        intent = GatewayDelegateIntent(self._route, command, canonical_gateway_payload(request.to_dict()), ttl_seconds)
        payload = self._validate(intent)
        with self._lock:
            try:
                old = self._host.load_delegate_intent(command.request_key)
                if old is None:
                    if self._host.save_delegate_intent(intent) != intent or self._host.load_delegate_intent(command.request_key) != intent:
                        raise ValueError
                elif old != intent:
                    raise ValueError
            except Exception:
                return GatewayDelegateResult("TRANSPORT_NOT_STARTED", code="INTENT_NOT_DURABLE")
            return self._rpc("direct.delegated_reserve", {"command": command.to_mapping(), "payload": payload,
                "ttl_seconds": ttl_seconds}, intent, timeout_seconds)

    def dispatch(self, request_key: str, *, market_ref=None, timeout_seconds=5.0):
        self._owned()
        if type(request_key) is not str or not request_key or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX:
            raise ValueError("INVALID_DELEGATE_REQUEST")
        normalized_market = None
        if market_ref is not None:
            from bt_api_base.market_protocol import MarketSubscriptionKey
            if (type(market_ref) is not dict or set(market_ref) != {'key', 'epoch', 'sequence', 'message_id'}
                    or type(market_ref['epoch']) is not str or not market_ref['epoch']
                    or type(market_ref['sequence']) is not int or market_ref['sequence'] < 1
                    or type(market_ref['message_id']) is not str or not market_ref['message_id']):
                return GatewayDelegateResult('TRANSPORT_NOT_STARTED', code='INVALID_MARKET_REFERENCE')
            try:
                key = MarketSubscriptionKey.from_dict(market_ref['key'])
                normalized_market = {'key': key.to_dict(), 'epoch': market_ref['epoch'],
                    'sequence': market_ref['sequence'], 'message_id': market_ref['message_id']}
            except Exception:
                return GatewayDelegateResult('TRANSPORT_NOT_STARTED', code='INVALID_MARKET_REFERENCE')
        with self._lock:
            try:
                intent = self._host.load_delegate_intent(request_key)
                payload = self._validate(intent)
                obs = self._host.load_delegate_observation(request_key)
                if (type(obs) is not GatewayDelegateObservation or obs.state != "RESERVED" or obs.request_key != request_key
                        or obs.account_scope != intent.command.account_scope
                        or obs.strategy_scope != intent.command.strategy_scope
                        or obs.version != 1 or type(obs.version) is not int
                        or obs.actor_principal != intent.command.actor_principal or obs.digest != intent.command.payload_digest
                        or type(obs.reservation_id) is not str or not obs.reservation_id
                        or obs.owner_generation != obs.reservation_generation):
                    raise ValueError
                marker = (obs.reservation_id, obs.digest, obs.version, intent.command.digest)
                existing = self._host.load_delegate_dispatch_marker(request_key)
                if existing is not None:
                    if existing != marker:
                        raise ValueError
                    # Only this client's own earlier attempt, proved never to have left, may be
                    # made again under the same identity; anything else may have reached the
                    # Gateway and is never sent twice.
                    if self._unsent_dispatch.get(request_key) != marker:
                        return GatewayDelegateResult("UNKNOWN", code="DISPATCH_ALREADY_ATTEMPTED")
                elif (self._host.save_delegate_dispatch_marker(request_key, marker) != marker
                        or self._host.load_delegate_dispatch_marker(request_key) != marker):
                    raise ValueError
            except Exception:
                return GatewayDelegateResult("TRANSPORT_NOT_STARTED", code="DISPATCH_NOT_DURABLE")
            arguments = {"command": intent.command.to_mapping(), "payload": payload,
                         "reservation_id": obs.reservation_id, "expected_version": obs.version}
            if normalized_market is not None:
                arguments['market_ref'] = normalized_market
            # The proof is used up by this attempt and recorded again only if it too never left.
            self._unsent_dispatch.pop(request_key, None)
            result = self._rpc("direct.delegated_dispatch", arguments, intent, timeout_seconds)
            if result.outcome == "TRANSPORT_NOT_STARTED":
                self._unsent_dispatch[request_key] = marker
            return result

    def _actor_call(self, operation, intent, arguments, timeout_seconds):
        try:
            registration = self._registration(operation)
        except Exception:
            return None, GatewayDelegateActorResult('TRANSPORT_NOT_STARTED', code='ROUTE_UNAVAILABLE')
        try:
            response = _detach_wire(self._runtime.call(self._route.mount, operation, arguments,
                account_ref=intent.command.account_scope, scope_ref=self._route.scope_ref, timeout_seconds=timeout_seconds))
        except Exception as error:
            outcome, code = runtime_failure(error, operation)
            return None, GatewayDelegateActorResult(outcome, code=code)
        try:
            if self._registration(operation) != registration or type(response) is not dict:
                raise ValueError
            if response.get('status') == 'REQUEST_REJECTED':
                if set(response) != {'status', 'code'} or type(response['code']) is not str:
                    raise ValueError
                return None, GatewayDelegateActorResult('REQUEST_DENIED', code=response['code'])
            if response == {'status': 'UNKNOWN'}:
                return None, GatewayDelegateActorResult('UNKNOWN')
            return response, None
        except Exception:
            return None, GatewayDelegateActorResult('UNKNOWN', code='INVALID_OR_UNAVAILABLE_REPLY')

    def actor_status(self, request_key, *, timeout_seconds=5.0):
        """Observe only this authenticated actor's original delegated command."""
        self._owned()
        if type(request_key) is not str or not request_key or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX:
            raise ValueError('INVALID_DELEGATE_REQUEST')
        with self._lock:
            self._actor_observations.pop(request_key, None)
            try:
                intent = self._host.load_delegate_intent(request_key)
                payload = self._validate(intent)
            except Exception:
                return GatewayDelegateActorResult('TRANSPORT_NOT_STARTED', code='INTENT_UNAVAILABLE')
            raw, error = self._actor_call('direct.delegate_actor_status', intent,
                {'command': intent.command.to_mapping(), 'payload': payload}, timeout_seconds)
            if error is not None:
                return error
            try:
                fields = set(GatewayDelegateObservation.__dataclass_fields__)
                if (set(raw) != fields | {'status', 'schema_version', 'native_identity_digest'}
                        or raw['status'] != 'OBSERVED' or type(raw['schema_version']) is not int or raw['schema_version'] != 1):
                    raise ValueError
                observation = GatewayDelegateObservation(**{name: raw[name] for name in fields})
                cmd = intent.command
                native_digest = raw['native_identity_digest']
                if (observation.account_scope != cmd.account_scope or observation.strategy_scope != cmd.strategy_scope
                        or observation.actor_principal != cmd.actor_principal or observation.request_key != request_key
                        or observation.digest != cmd.payload_digest or type(observation.version) is not int
                        or type(observation.state) is not str or {'RESERVED': 1, 'DISPATCHING': 2, 'SENT': 3,
                            'NOT_SENT': 3, 'UNKNOWN': 3, 'RELEASED': 2, 'EXPIRED': 2}.get(observation.state) != observation.version
                        or type(observation.reservation_id) is not str or not observation.reservation_id
                        or type(observation.owner_generation) is not str or not observation.owner_generation
                        or type(observation.reservation_generation) is not str or not observation.reservation_generation
                        or (native_digest is not None and (type(native_digest) is not str or len(native_digest) != 64
                            or any(c not in '0123456789abcdef' for c in native_digest)))
                        or (observation.state in {'DISPATCHING', 'SENT', 'NOT_SENT', 'UNKNOWN'} and native_digest is None)):
                    raise ValueError
                if (self._host.save_delegate_observation(request_key, observation) != observation
                        or self._host.load_delegate_observation(request_key) != observation):
                    raise ValueError
                result = GatewayDelegateActorObservation(observation, native_digest)
                self._actor_observations[request_key] = result
                return GatewayDelegateActorResult('OBSERVED', observation=result)
            except Exception:
                return GatewayDelegateActorResult('UNKNOWN', code='INVALID_OR_UNAVAILABLE_REPLY')

    def _cancel_actor_result(self, operation, intent, plan, timeout_seconds):
        payload = self._validate(intent)
        args = {'command': intent.command.to_mapping(), 'payload': payload, 'operation_id': plan.operation_id}
        if operation == 'direct.delegate_actor_cancel':
            args['reservation_id'] = plan.reservation_id
        raw, error = self._actor_call(operation, intent, args, timeout_seconds)
        if error is not None:
            return error
        try:
            fields = {'status', 'record'} | ({'cancel_queue_state'} if operation == 'direct.delegate_actor_cancel' else set())
            if set(raw) != fields or raw['status'] != 'OK' or type(raw['record']) is not dict:
                raise ValueError
            if operation == 'direct.delegate_actor_cancel' and raw['cancel_queue_state'] not in {'QUEUED', 'ALREADY_QUEUED', 'OBSERVED'}:
                raise ValueError
            record = GatewayDelegatedCancelObservation(**raw['record'])
            if (record.account_scope != intent.command.account_scope or record.strategy_scope != intent.command.strategy_scope
                    or record.requester_principal != plan.actor_principal or record.target_actor_principal != plan.actor_principal
                    or record.request_key != plan.request_key or record.operation_id != plan.operation_id
                    or record.target_reservation_id != plan.reservation_id or record.target_digest != plan.payload_digest
                    or type(record.state) is not str or type(record.version) is not int
                    or {'REQUESTED': 1, 'DISPATCHING': 2, 'SENT': 3, 'NOT_SENT': 3, 'UNKNOWN': 3}.get(record.state) != record.version
                    or any(type(v) is not str or not v for v in (record.attempt_id, record.owner_generation, record.attempt_generation))):
                raise ValueError
            if (self._host.save_delegate_cancel_observation(plan.operation_id, record) != record
                    or self._host.load_delegate_cancel_observation(plan.operation_id) != record):
                raise ValueError
            queue_state = raw.get('cancel_queue_state')
            return GatewayDelegateActorResult('QUEUED' if queue_state in {'QUEUED', 'ALREADY_QUEUED'}
                and record.state == 'REQUESTED' else 'OBSERVED', cancel=record)
        except Exception:
            return GatewayDelegateActorResult('UNKNOWN', code='INVALID_OR_UNAVAILABLE_REPLY')

    def cancel_actor(self, observation, *, operation_id, timeout_seconds=5.0):
        """Persist one cancel and marker before calling Gateway; never resend it."""
        self._owned()
        if (type(observation) is not GatewayDelegateActorObservation or type(operation_id) is not str
                or not operation_id or operation_id != operation_id.strip() or len(operation_id) > 256
                or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError('INVALID_DELEGATE_REQUEST')
        with self._lock:
            try:
                row = observation.command
                if (self._actor_observations.get(row.request_key) is not observation
                        or row.state not in {'DISPATCHING', 'SENT', 'UNKNOWN'} or observation.native_identity_digest is None):
                    raise ValueError
                intent = self._host.load_delegate_intent(row.request_key)
                self._validate(intent)
                plan = GatewayDelegateActorCancelIntent(row.request_key, operation_id, intent.command.digest,
                    row.reservation_id, row.digest, row.actor_principal, observation.native_identity_digest)
                existing = self._host.load_delegate_cancel_intent(operation_id)
                if existing is not None:
                    if existing != plan:
                        raise ValueError
                    return self._cancel_actor_result('direct.delegate_actor_cancel_status', intent, plan, timeout_seconds)
                if (self._host.save_delegate_cancel_intent(plan) != plan
                        or self._host.load_delegate_cancel_intent(operation_id) != plan):
                    raise ValueError
                marker = (plan.request_key, plan.reservation_id, plan.payload_digest, plan.command_digest, plan.native_identity_digest)
                if (self._host.save_delegate_cancel_marker(operation_id, marker) != marker
                        or self._host.load_delegate_cancel_marker(operation_id) != marker):
                    raise ValueError
            except Exception:
                return GatewayDelegateActorResult('TRANSPORT_NOT_STARTED', code='CANCEL_NOT_DURABLE')
            return self._cancel_actor_result('direct.delegate_actor_cancel', intent, plan, timeout_seconds)

    def actor_cancel_status(self, operation_id, *, timeout_seconds=5.0):
        self._owned()
        if (type(operation_id) is not str or not operation_id or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError('INVALID_DELEGATE_REQUEST')
        with self._lock:
            try:
                plan = self._host.load_delegate_cancel_intent(operation_id)
                if type(plan) is not GatewayDelegateActorCancelIntent or plan.operation_id != operation_id:
                    raise ValueError
                intent = self._host.load_delegate_intent(plan.request_key)
                self._validate(intent)
            except Exception:
                return GatewayDelegateActorResult('TRANSPORT_NOT_STARTED', code='CANCEL_PLAN_UNAVAILABLE')
            return self._cancel_actor_result('direct.delegate_actor_cancel_status', intent, plan, timeout_seconds)

    def _release_query(self, intent, plan, timeout_seconds):
        payload = self._validate(intent)
        raw, error = self._actor_call('direct.delegate_actor_release_status', intent,
            {'command': intent.command.to_mapping(), 'payload': payload, 'operation_id': plan.operation_id}, timeout_seconds)
        if error is not None:
            return GatewayDelegateActorReleaseResult(error.outcome, code=error.code)
        try:
            fields = set(GatewayDelegateObservation.__dataclass_fields__)
            if (set(raw) != fields | {'status', 'schema_version', 'native_identity_digest', 'release_operation_id', 'release_expected_version'}
                    or raw['status'] != 'OBSERVED' or type(raw['schema_version']) is not int or raw['schema_version'] != 1
                    or raw['release_operation_id'] != plan.operation_id or raw['release_expected_version'] != plan.expected_version):
                raise ValueError
            observation = GatewayDelegateObservation(**{name: raw[name] for name in fields})
            if (observation.account_scope != intent.command.account_scope or observation.strategy_scope != intent.command.strategy_scope
                    or observation.actor_principal != plan.actor_principal or observation.request_key != plan.request_key
                    or observation.reservation_id != plan.reservation_id or observation.digest != plan.payload_digest
                    or observation.state not in {'RELEASED', 'EXPIRED'} or type(observation.version) is not int
                    or observation.version != 2 or raw['native_identity_digest'] is not None
                    or type(observation.owner_generation) is not str or not observation.owner_generation
                    or observation.owner_generation != observation.reservation_generation):
                raise ValueError
            if (self._host.save_delegate_observation(plan.request_key, observation) != observation
                    or self._host.load_delegate_observation(plan.request_key) != observation):
                raise ValueError
            return GatewayDelegateActorReleaseResult('OBSERVED', observation.state, plan.reservation_id, plan.operation_id)
        except Exception:
            return GatewayDelegateActorReleaseResult('UNKNOWN', code='INVALID_OR_UNAVAILABLE_REPLY')

    def release_actor(self, observation, *, operation_id, timeout_seconds=5.0):
        """Persist a frozen release key before one call, then verify that exact operation."""
        self._owned()
        if (type(observation) is not GatewayDelegateActorObservation or type(operation_id) is not str
                or not operation_id or operation_id != operation_id.strip() or len(operation_id) > 256
                or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError('INVALID_DELEGATE_REQUEST')
        with self._lock:
            try:
                row = observation.command
                if self._actor_observations.get(row.request_key) is not observation or row.state != 'RESERVED':
                    raise ValueError
                intent = self._host.load_delegate_intent(row.request_key)
                payload = self._validate(intent)
                plan = GatewayDelegateActorReleaseIntent(row.request_key, operation_id, intent.command.digest,
                    row.reservation_id, row.digest, row.actor_principal, row.version)
                existing = self._host.load_delegate_release_intent(operation_id)
                if existing is not None:
                    if existing != plan:
                        raise ValueError
                    return self._release_query(intent, plan, timeout_seconds)
                if (self._host.save_delegate_release_intent(plan) != plan
                        or self._host.load_delegate_release_intent(operation_id) != plan):
                    raise ValueError
                marker = (plan.request_key, plan.reservation_id, plan.payload_digest, plan.command_digest, plan.expected_version)
                if (self._host.save_delegate_release_marker(operation_id, marker) != marker
                        or self._host.load_delegate_release_marker(operation_id) != marker):
                    raise ValueError
            except Exception:
                return GatewayDelegateActorReleaseResult('TRANSPORT_NOT_STARTED', code='RELEASE_NOT_DURABLE')
            _raw, error = self._actor_call('direct.delegate_actor_release', intent,
                {'command': intent.command.to_mapping(), 'payload': payload, 'reservation_id': plan.reservation_id,
                 'operation_id': plan.operation_id, 'expected_version': plan.expected_version}, timeout_seconds)
            if error is not None:
                return GatewayDelegateActorReleaseResult(error.outcome, code=error.code)
            return self._release_query(intent, plan, timeout_seconds)

    def actor_release_status(self, operation_id, *, timeout_seconds=5.0):
        self._owned()
        if type(operation_id) is not str or not operation_id or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX:
            raise ValueError('INVALID_DELEGATE_REQUEST')
        with self._lock:
            try:
                plan = self._host.load_delegate_release_intent(operation_id)
                if type(plan) is not GatewayDelegateActorReleaseIntent or plan.operation_id != operation_id:
                    raise ValueError
                intent = self._host.load_delegate_intent(plan.request_key)
                self._validate(intent)
            except Exception:
                return GatewayDelegateActorReleaseResult('TRANSPORT_NOT_STARTED', code='RELEASE_PLAN_UNAVAILABLE')
            return self._release_query(intent, plan, timeout_seconds)
