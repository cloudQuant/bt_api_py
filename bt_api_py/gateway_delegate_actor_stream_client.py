"""Authenticated SERVICE actor stream client; application ACK is explicit."""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from dataclasses import dataclass

from bt_api_base.gateway_execution_stream_protocol import GatewayExecutionStreamEvent
from bt_api_base.service_protocol import WireScope
from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute
from bt_api_py.gateway_execution_stream_client import _detach_wire, _epoch, _sequence
from bt_api_py.service_runtime import ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime


@dataclass(frozen=True, slots=True)
class DelegateActorEvent:
    event: GatewayExecutionStreamEvent
    origin: str
    on_behalf_of: str
    parent_ref: str | None
    child_id: str | None


@dataclass(frozen=True, slots=True)
class DelegateActorProgress:
    epoch: str
    offered_sequence: int
    acknowledged_sequence: int


@dataclass(frozen=True, slots=True)
class DelegateActorResult:
    outcome: str
    progress: DelegateActorProgress | None = None
    events: tuple[DelegateActorEvent, ...] = ()
    has_more: bool = False
    code: str | None = None


class GatewayDelegateActorStreamClient:
    def __init__(self, runtime, *, route, grant_revision):
        if (type(runtime) is not ThreadedServiceClientRuntime or type(route) is not GatewayDelegateRoute
                or type(grant_revision) is not str or not grant_revision or grant_revision != grant_revision.strip()):
            raise ValueError('INVALID_ACTOR_STREAM_CLIENT')
        self._runtime, self._route = runtime, route
        identity, scope = route.identity, route.scope
        raw = [identity.principal_ref, identity.role.value, identity.actor_ref, scope.execution_domain_ref,
               scope.account_ref, scope.strategy_ref, scope.run_id, grant_revision]
        self._consumer = 'delegate-actor:' + hashlib.sha256(json.dumps(raw, separators=(',', ':'),
            ensure_ascii=True).encode('ascii')).hexdigest()
        self._pid, self._thread, self._lock = os.getpid(), threading.current_thread(), threading.Lock()

    def _registration(self, operation):
        snap, route = self._runtime.status(), self._route
        matches = [b for b in snap.bindings if b.active is True and b.required is True and b.mount == route.mount
                   and b.service_id == route.service_id and b.instance_id == route.instance_id
                   and b.scope_ref == route.scope_ref and type(b.scope) is WireScope and b.scope == route.scope
                   and type(b.operations) is frozenset and operation in b.operations]
        if (snap.state != 'running' or not snap.thread_alive or not snap.healthy or snap.identity != route.identity
                or snap.run_id != route.scope.run_id or len(matches) != 1
                or type(matches[0].registration_id) is not str or not matches[0].registration_id
                or type(matches[0].service_epoch) is not str or not matches[0].service_epoch):
            raise ValueError('ROUTE_UNAVAILABLE')
        return matches[0].registration_id, matches[0].service_epoch

    def _call(self, operation, arguments, timeout_seconds):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError('ACTOR_STREAM_WRONG_OWNER')
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError('INVALID_TIMEOUT')
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('ACTOR_STREAM_BUSY')
        try:
            try:
                registration = self._registration(operation)
            except Exception:
                return DelegateActorResult('TRANSPORT_NOT_STARTED', code='ROUTE_UNAVAILABLE')
            try:
                reply = _detach_wire(self._runtime.call(self._route.mount, operation, arguments,
                    account_ref=self._route.scope.account_ref, scope_ref=self._route.scope_ref,
                    timeout_seconds=timeout_seconds))
            except ServiceRuntimeRequestNotSentError:
                return DelegateActorResult('TRANSPORT_NOT_STARTED', code='REQUEST_NOT_SENT')
            except Exception:
                return DelegateActorResult('UNKNOWN', code='RPC_OUTCOME_UNKNOWN')
            try:
                if self._registration(operation) != registration or type(reply) is not dict:
                    raise ValueError
                if reply.get('status') == 'REQUEST_REJECTED':
                    if set(reply) != {'status', 'code'} or reply['code'] not in {'INVALID_REQUEST', 'SCOPE_DENIED',
                            'INVALID_SEQUENCE', 'EPOCH_MISMATCH', 'CONSUMER_CAPACITY_EXCEEDED',
                            'ACTOR_STREAM_GAP', 'ACTOR_STREAM_UNAVAILABLE'}:
                        raise ValueError
                    return DelegateActorResult('REQUEST_DENIED', code=reply['code'])
                field = 'page' if operation.endswith('read_after') else 'progress'
                if set(reply) != {'status', field} or reply['status'] != 'OK' or type(reply[field]) is not dict:
                    raise ValueError
                raw = reply[field]
                keys = {'schema_version', 'account_scope', 'strategy_scope', 'actor_principal', 'consumer_ref',
                        'epoch', 'offered_sequence', 'acknowledged_sequence'}
                if field == 'page':
                    keys |= {'has_more', 'events'}
                scope, identity = self._route.scope, self._route.identity
                if (set(raw) != keys or type(raw['schema_version']) is not int or raw['schema_version'] != 1
                        or raw['account_scope'] != scope.account_ref or raw['strategy_scope'] != scope.strategy_ref
                        or raw['actor_principal'] != identity.principal_ref or raw['consumer_ref'] != self._consumer
                        or not _epoch(raw['epoch']) or not _sequence(raw['offered_sequence'])
                        or not _sequence(raw['acknowledged_sequence'])
                        or raw['acknowledged_sequence'] > raw['offered_sequence']
                        or (arguments.get('expected_epoch') is not None and arguments['expected_epoch'] != raw['epoch'])):
                    raise ValueError
                progress = DelegateActorProgress(raw['epoch'], raw['offered_sequence'], raw['acknowledged_sequence'])
                if field == 'progress':
                    if operation.endswith('.ack') and progress.acknowledged_sequence < arguments['sequence']:
                        raise ValueError
                    return DelegateActorResult('OBSERVED', progress=progress)
                if (type(raw['events']) is not list or len(raw['events']) > arguments['limit']
                        or type(raw['has_more']) is not bool or (not raw['events'] and raw['has_more'])):
                    raise ValueError
                events, previous = [], arguments['after_sequence']
                for item in raw['events']:
                    if type(item) is not dict:
                        raise ValueError
                    data = dict(item)
                    origin, strategy = data.pop('origin'), data.pop('on_behalf_of')
                    parent, child = data.pop('parent_ref'), data.pop('child_id')
                    event = GatewayExecutionStreamEvent.from_mapping(data)
                    if (event.sequence <= previous or event.sequence > progress.offered_sequence
                            or event.command_principal != identity.principal_ref
                            or event.observation.account_scope != scope.account_ref or strategy != scope.strategy_ref
                            or origin not in {'risk:' + identity.principal_ref, 'execution:' + identity.principal_ref}
                            or (parent is None) != (child is None)
                            or (parent is not None and any(type(v) is not str or not v for v in (parent, child)))
                            or (origin.startswith('execution:') and parent is None)):
                        raise ValueError
                    previous = event.sequence
                    events.append(DelegateActorEvent(event, origin, strategy, parent, child))
                if previous < progress.offered_sequence and not raw['has_more']:
                    raise ValueError
                return DelegateActorResult('OBSERVED', progress=progress, events=tuple(events), has_more=raw['has_more'])
            except Exception:
                return DelegateActorResult('UNKNOWN', code='INVALID_OR_UNAVAILABLE_REPLY')
        finally:
            self._lock.release()

    def read_after(self, *, after_sequence=0, expected_epoch=None, limit=256, timeout_seconds=5.0):
        if (not _sequence(after_sequence) or type(limit) is not int or not 1 <= limit <= 256
                or (expected_epoch is not None and not _epoch(expected_epoch)) or (after_sequence and expected_epoch is None)):
            raise ValueError('INVALID_READ')
        return self._call('direct.delegate_events.read_after', {'after_sequence': after_sequence,
            'expected_epoch': expected_epoch, 'limit': limit}, timeout_seconds)

    def progress(self, *, timeout_seconds=5.0):
        return self._call('direct.delegate_events.progress', {}, timeout_seconds)

    def acknowledge(self, sequence, *, expected_epoch, timeout_seconds=5.0):
        if not _sequence(sequence) or not _epoch(expected_epoch):
            raise ValueError('INVALID_ACK')
        return self._call('direct.delegate_events.ack', {'sequence': sequence, 'expected_epoch': expected_epoch}, timeout_seconds)
