"""Independent account event observer; reads never acknowledge automatically."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from dataclasses import dataclass, field, replace

from bt_api_base.gateway_execution_stream_protocol import GatewayExecutionStreamEvent
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_delegate_service_client import runtime_failure
from bt_api_py.gateway_execution_stream_client import _detach_wire, _epoch, _sequence
from bt_api_py.service_runtime_thread import (ThreadedServiceClientRuntime, BoundCallRoute,
    BoundCallObservation, BoundCallResult, _BoundBindingToken)


@dataclass(frozen=True, slots=True)
class GatewayAccountObserverRoute:
    mount: str
    service_id: str
    instance_id: str
    scope_ref: str
    scope: WireScope
    identity: WireIdentity

    def __post_init__(self):
        if (type(self.identity) is not WireIdentity
                or self.mount != {WireRole.STRATEGY_CLIENT: 'store.account',
                                  WireRole.SERVICE: 'upstream.account',
                                  WireRole.APPLICATION_CLIENT: 'application.account'}.get(self.identity.role)
                or any(type(v) is not str or not v or v != v.strip() or len(v) > 256
                for v in (self.service_id, self.instance_id, self.scope_ref))
                or self.identity.role not in {WireRole.STRATEGY_CLIENT, WireRole.SERVICE, WireRole.APPLICATION_CLIENT}
                or self.identity.delegate_ref is not None or type(self.scope) is not WireScope
                or not self.scope.account_ref or self.scope.account_refs or self.scope.manual_book_ref is not None
                or self.scope.portfolio_ref is not None or self.scope.group_id is not None or self.scope.leg_id is not None
                or (self.identity.role is WireRole.STRATEGY_CLIENT and
                    (self.scope.strategy_ref != self.identity.actor_ref or not self.scope.run_id))
                or (self.identity.role is not WireRole.STRATEGY_CLIENT and
                    (self.scope.strategy_ref is not None or self.scope.run_id is not None))):
            raise ValueError('INVALID_ACCOUNT_OBSERVER_ROUTE')


@dataclass(frozen=True, slots=True)
class GatewayAccountObservedEvent:
    strategy_scope: str
    event: GatewayExecutionStreamEvent


@dataclass(frozen=True, slots=True)
class GatewayAccountObserverProgress:
    account_scope: str
    consumer_ref: str
    epoch: str
    offered_sequence: int
    acknowledged_sequence: int


@dataclass(frozen=True, slots=True)
class GatewayAccountObserverPage:
    progress: GatewayAccountObserverProgress
    events: tuple[GatewayAccountObservedEvent, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class GatewayAccountObserverResult:
    outcome: str
    page: GatewayAccountObserverPage | None = None
    progress: GatewayAccountObserverProgress | None = None
    code: str | None = None
    _read_token: object = field(default=None, repr=False, compare=False)


class GatewayAccountObserverClient:
    """Read-only service access; caller owns durable apply then ACK.

    Supplying both peer and fingerprint binds each response to that authenticated
    call. It does not grant a continuing lease through later database commits or
    remove Risk's v1 source quarantine.
    """
    def __init__(self, runtime: ThreadedServiceClientRuntime, *, route: GatewayAccountObserverRoute,
                 peer: WireIdentity | None = None, fingerprint: str | None = None):
        if type(runtime) is not ThreadedServiceClientRuntime or type(route) is not GatewayAccountObserverRoute:
            raise ValueError('INVALID_ACCOUNT_OBSERVER_CLIENT')
        self._runtime, self._route = runtime, route
        if peer is None and fingerprint is None:
            self._bound_route = None
        elif (type(peer) is WireIdentity and peer.role is WireRole.SERVICE and peer.delegate_ref is None
              and type(fingerprint) is str and re.fullmatch('[0-9a-f]{64}', fingerprint)):
            self._bound_route = BoundCallRoute(route.identity, route.scope, route.scope_ref,
                                                route.service_id, route.instance_id, peer)
        else:
            raise ValueError('INVALID_ACCOUNT_OBSERVER_BOUND_ROUTE')
        self._expected_fingerprint = fingerprint
        self._read_binding = None
        self._pid, self._thread, self._lock = os.getpid(), threading.current_thread(), threading.Lock()
        identity, scope = route.identity, route.scope
        self._consumer_ref = 'account-observer:' + hashlib.sha256(json.dumps([
            identity.principal_ref, identity.role.value, identity.actor_ref, scope.execution_domain_ref,
            scope.account_ref, scope.strategy_ref], separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()

    def _registration(self, operation):
        snap, route = self._runtime.status(), self._route
        entries = [item for item in snap.bindings if item.active is True and item.required is True
            and item.mount == route.mount and item.service_id == route.service_id and item.instance_id == route.instance_id
            and item.scope_ref == route.scope_ref and type(item.scope) is WireScope and item.scope == route.scope
            and type(item.operations) is frozenset and operation in item.operations]
        if (snap.state != 'running' or not snap.thread_alive or not snap.healthy or snap.identity != route.identity
                or (route.identity.role is WireRole.STRATEGY_CLIENT and snap.run_id != route.scope.run_id)
                or len(entries) != 1
                or type(entries[0].registration_id) is not str or not entries[0].registration_id
                or type(entries[0].service_epoch) is not str or not entries[0].service_epoch):
            raise ValueError('ROUTE_UNAVAILABLE')
        return entries[0].registration_id, entries[0].service_epoch

    def _call(self, operation, arguments, timeout_seconds, *, read_token=None):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError('ACCOUNT_OBSERVER_WRONG_OWNER')
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX:
            raise ValueError('INVALID_TIMEOUT')
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('ACCOUNT_OBSERVER_BUSY')
        try:
            issued = None
            if self._bound_route is not None:
                if operation == 'account.events.read_after':
                    self._read_binding = None
                elif operation == 'account.events.ack':
                    issued = self._read_binding
                    if (issued is None or read_token is not issued[0] or issued[1] != issued[2]
                            or arguments['expected_epoch'] != issued[3]
                            or not issued[4] < arguments['sequence'] <= issued[5]
                            or self._runtime is not issued[6]):
                        return GatewayAccountObserverResult('TRANSPORT_NOT_STARTED', code='READ_BINDING_REQUIRED')
                    self._read_binding = None
            try:
                registration = self._registration(operation)
            except Exception:
                return GatewayAccountObserverResult('TRANSPORT_NOT_STARTED', code='ROUTE_UNAVAILABLE')
            try:
                if self._bound_route is None:
                    result = _detach_wire(self._runtime.call(self._route.mount, operation, arguments,
                        account_ref=self._route.scope.account_ref, scope_ref=self._route.scope_ref,
                        timeout_seconds=timeout_seconds))
                    bound = None
                else:
                    kwargs = {'route': self._bound_route, 'timeout_seconds': timeout_seconds}
                    if issued is not None:
                        kwargs.update(expected_binding=issued[1], _expected_token=issued[7])
                    bound = self._runtime.call_bound(self._route.mount, operation, arguments, **kwargs)
                    result = _detach_wire(bound.result) if type(bound) is BoundCallResult else None
            except Exception as error:
                # Not started only when provably never sent (G64-08). A Registry refusal is
                # not mapped: it may follow a cursor effect of the handler.
                outcome, code = runtime_failure(error)
                return GatewayAccountObserverResult(outcome, code=code)
            try:
                if bound is not None:
                    before = bound.before
                    if (type(before) is not BoundCallObservation or type(bound.after) is not BoundCallObservation
                            or before != bound.after or (before.registration_id, before.service_epoch) != registration
                            or before.identity != self._route.identity or before.scope != self._route.scope
                            or before.peer != self._bound_route.peer or before.fingerprint != self._expected_fingerprint
                            or before.mount != self._route.mount or before.service_id != self._route.service_id
                            or before.instance_id != self._route.instance_id or before.scope_ref != self._route.scope_ref
                            or before.operation != operation or operation not in before.operations
                            or before.active is not True or before.required is not True
                            or type(before.lease_deadline) not in (int, float)
                            or not math.isfinite(before.lease_deadline) or before.lease_deadline <= 0):
                        raise ValueError
                if self._registration(operation) != registration or type(result) is not dict:
                    raise ValueError
                if result.get('status') == 'REQUEST_REJECTED':
                    if set(result) != {'status', 'code'} or result['code'] not in {
                        'INVALID_REQUEST', 'SCOPE_DENIED', 'INVALID_SEQUENCE', 'EPOCH_MISMATCH',
                        'CONSUMER_CAPACITY_EXCEEDED', 'EVENTS_UNAVAILABLE',
                        'CONSUMER_REPLAY_REQUIRED', 'ACCOUNT_OBSERVER_HANDOFF_UNAVAILABLE'}:
                        raise ValueError
                    return GatewayAccountObserverResult('REQUEST_DENIED', code=result['code'])
                field = 'page' if operation == 'account.events.read_after' else 'progress'
                if set(result) != {'status', field} or result['status'] != 'OK' or type(result[field]) is not dict:
                    raise ValueError
                raw = result[field]
                fields = {'schema_version', 'account_scope', 'consumer_ref', 'epoch',
                          'offered_sequence', 'acknowledged_sequence'}
                if field == 'page':
                    fields |= {'has_more', 'events'}
                if (set(raw) != fields or type(raw['schema_version']) is not int or raw['schema_version'] != 1
                        or raw['account_scope'] != self._route.scope.account_ref or raw['consumer_ref'] != self._consumer_ref
                        or not _epoch(raw['epoch']) or not _sequence(raw['offered_sequence'])
                        or not _sequence(raw['acknowledged_sequence']) or raw['acknowledged_sequence'] > raw['offered_sequence']
                        or (arguments.get('expected_epoch') is not None and raw['epoch'] != arguments['expected_epoch'])):
                    raise ValueError
                progress = GatewayAccountObserverProgress(*(raw[key] for key in ('account_scope', 'consumer_ref', 'epoch',
                    'offered_sequence', 'acknowledged_sequence')))
                if field == 'progress':
                    if operation == 'account.events.ack' and progress.acknowledged_sequence < arguments['sequence']:
                        raise ValueError
                    return GatewayAccountObserverResult('OBSERVED', progress=progress)
                if type(raw['has_more']) is not bool or type(raw['events']) is not list or len(raw['events']) > arguments['limit']:
                    raise ValueError
                events = []
                previous = arguments['after_sequence']
                contiguous = previous
                for item in raw['events']:
                    if type(item) is not dict:
                        raise ValueError
                    item = dict(item)
                    strategy = item.pop('strategy_scope', None)
                    event = GatewayExecutionStreamEvent.from_mapping(item)
                    attribution = event.observation.trade_attribution
                    if (type(strategy) is not str or not strategy or strategy != strategy.strip() or len(strategy) > 256
                            or event.sequence <= previous or event.sequence > progress.offered_sequence
                            or event.observation.account_scope != progress.account_scope
                            or (attribution is not None and attribution.strategy_scope != strategy)):
                        raise ValueError
                    previous = event.sequence
                    if event.sequence == contiguous + 1:
                        contiguous = event.sequence
                    events.append(GatewayAccountObservedEvent(strategy, event))
                token = None
                if bound is not None and contiguous > arguments['after_sequence']:
                    if type(bound._binding_token) is not _BoundBindingToken:
                        raise ValueError
                    token = object()
                    snapshot = replace(bound.before, identity=WireIdentity(**vars(bound.before.identity)),
                                       scope=WireScope(**vars(bound.before.scope)),
                                       peer=WireIdentity(**vars(bound.before.peer)))
                    self._read_binding = (token, bound.before, snapshot, raw['epoch'],
                                          arguments['after_sequence'], contiguous, self._runtime, bound._binding_token)
                return GatewayAccountObserverResult('OBSERVED', page=GatewayAccountObserverPage(progress, tuple(events), raw['has_more']), _read_token=token)
            except Exception:
                return GatewayAccountObserverResult('UNKNOWN', code='INVALID_OR_UNAVAILABLE_REPLY')
        finally:
            self._lock.release()

    def read_after(self, *, after_sequence=0, expected_epoch=None, limit=256, timeout_seconds=5.0):
        if (not _sequence(after_sequence) or type(limit) is not int or not 1 <= limit <= 256
                or (expected_epoch is not None and not _epoch(expected_epoch)) or (after_sequence and expected_epoch is None)):
            raise ValueError('INVALID_READ')
        return self._call('account.events.read_after', {'after_sequence': after_sequence,
            'expected_epoch': expected_epoch, 'limit': limit}, timeout_seconds)

    def progress(self, *, timeout_seconds=5.0):
        return self._call('account.events.progress', {}, timeout_seconds)

    def acknowledge(self, sequence, *, expected_epoch, timeout_seconds=5.0, read_token=None):
        if not _sequence(sequence) or not _epoch(expected_epoch):
            raise ValueError('INVALID_ACK')
        return self._call('account.events.ack', {'sequence': sequence, 'expected_epoch': expected_epoch}, timeout_seconds,
                          read_token=read_token)
