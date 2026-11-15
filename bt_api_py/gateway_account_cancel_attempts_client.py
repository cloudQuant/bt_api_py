"""Independent cancel stream client. Durable consumers must apply before ACK."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from dataclasses import dataclass

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py.gateway_account_observer_client import GatewayAccountObserverClient, GatewayAccountObserverRoute
from bt_api_py.gateway_delegate_service_client import runtime_failure
from bt_api_py.gateway_execution_stream_client import _detach_wire, _epoch, _sequence
from bt_api_py.service_runtime_thread import BoundCallObservation, BoundCallResult, BoundCallRoute, _BoundBindingToken


@dataclass(frozen=True, slots=True)
class CancelAttemptResult:
    outcome: str
    page: dict | None = None
    progress: dict | None = None
    code: str | None = None
    binding: BoundCallObservation | None = None


class GatewayAccountCancelAttemptsClient(GatewayAccountObserverClient):
    def __init__(self, runtime, *, route: GatewayAccountObserverRoute, peer: WireIdentity, fingerprint: str):
        super().__init__(runtime, route=route)
        if (route.identity.role not in {WireRole.SERVICE, WireRole.APPLICATION_CLIENT}
                or type(peer) is not WireIdentity or peer.role is not WireRole.SERVICE
                or peer.delegate_ref is not None or type(fingerprint) is not str
                or re.fullmatch('[0-9a-f]{64}', fingerprint) is None):
            raise ValueError('INVALID_CANCEL_STREAM_ROUTE')
        self._bound_route = BoundCallRoute(route.identity, route.scope, route.scope_ref,
                                           route.service_id, route.instance_id, peer)
        self._expected_fingerprint = fingerprint
        identity, scope = route.identity, route.scope
        self._consumer_ref = 'cancel-attempts:' + hashlib.sha256(json.dumps([
            identity.principal_ref, identity.role.value, identity.actor_ref, scope.execution_domain_ref,
            scope.account_ref], separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()
        self._read_binding = None

    def _call(self, operation, arguments, timeout_seconds, *, expected_binding=None):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError('CANCEL_STREAM_WRONG_OWNER')
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX:
            raise ValueError('INVALID_TIMEOUT')
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('CANCEL_STREAM_BUSY')
        try:
            if operation.endswith('.read_after'):
                self._read_binding = None
            elif operation.endswith('.ack'):
                issued = self._read_binding
                if (issued is None or expected_binding is not issued[0]
                        or expected_binding != issued[1]
                        or arguments['expected_epoch'] != issued[2]
                        or arguments['sequence'] != issued[3] or self._runtime is not issued[4]):
                    return CancelAttemptResult('TRANSPORT_NOT_STARTED', code='READ_BINDING_REQUIRED')
                # An unknown ACK outcome must be resolved by a fresh read.
                self._read_binding = None
            try:
                registration = self._registration(operation)
            except Exception:
                return CancelAttemptResult('TRANSPORT_NOT_STARTED', code='ROUTE_UNAVAILABLE')
            try:
                kwargs = {'route': self._bound_route, 'timeout_seconds': timeout_seconds}
                if expected_binding is not None:
                    kwargs['expected_binding'] = expected_binding
                    kwargs['_expected_token'] = issued[5]
                bound = self._runtime.call_bound(self._route.mount, operation, arguments, **kwargs)
            except Exception as error:
                outcome, code = runtime_failure(error)
                return CancelAttemptResult(outcome, code=code)
            try:
                if (type(bound) is not BoundCallResult or type(bound.before) is not BoundCallObservation
                        or type(bound.after) is not BoundCallObservation or bound.before != bound.after
                        or (bound.before.registration_id, bound.before.service_epoch) != registration
                        or bound.before.identity != self._route.identity or bound.before.scope != self._route.scope
                        or bound.before.peer != self._bound_route.peer
                        or bound.before.fingerprint != self._expected_fingerprint
                        or bound.before.mount != self._route.mount or bound.before.service_id != self._route.service_id
                        or bound.before.instance_id != self._route.instance_id or bound.before.scope_ref != self._route.scope_ref
                        or bound.before.operation != operation or operation not in bound.before.operations
                        or bound.before.active is not True or bound.before.required is not True
                        or type(bound.before.lease_deadline) not in (int, float)
                        or not math.isfinite(bound.before.lease_deadline) or bound.before.lease_deadline <= 0
                        or self._registration(operation) != registration):
                    raise ValueError
                result = _detach_wire(bound.result)
                if type(result) is not dict:
                    raise ValueError
                if result.get('status') == 'REQUEST_REJECTED':
                    if set(result) != {'status', 'code'} or result['code'] not in {
                            'INVALID_INPUT', 'INVALID_REQUEST', 'SCOPE_DENIED', 'INVALID_SEQUENCE',
                            'EPOCH_MISMATCH', 'CONSUMER_CAPACITY_EXCEEDED', 'CONSUMER_REPLAY_REQUIRED'}:
                        raise ValueError
                    return CancelAttemptResult('REQUEST_DENIED', code=result['code'], binding=bound.before)
                field = 'page' if operation.endswith('.read_after') else 'progress'
                if set(result) != {'status', field} or result['status'] != 'OK' or type(result[field]) is not dict:
                    raise ValueError
                raw = result[field]
                fields = {'schema_version', 'account_scope', 'consumer_ref', 'epoch', 'offered_sequence',
                          'acknowledged_sequence', 'history_complete', 'overflow'}
                if field == 'page':
                    fields |= {'events', 'has_more'}
                if (set(raw) != fields or type(raw['schema_version']) is not int or raw['schema_version'] != 1
                        or raw['account_scope'] != self._route.scope.account_ref or raw['consumer_ref'] != self._consumer_ref
                        or not _epoch(raw['epoch']) or not _sequence(raw['offered_sequence'])
                        or not _sequence(raw['acknowledged_sequence']) or raw['acknowledged_sequence'] > raw['offered_sequence']
                        or raw['history_complete'] is not False or type(raw['overflow']) is not bool
                        or (arguments.get('expected_epoch') is not None and raw['epoch'] != arguments['expected_epoch'])):
                    raise ValueError
                if field == 'progress':
                    if operation.endswith('.ack') and raw['acknowledged_sequence'] != arguments['sequence']:
                        raise ValueError
                    return CancelAttemptResult('OBSERVED', progress=raw, binding=bound.before)
                if type(raw['has_more']) is not bool or type(raw['events']) is not list or len(raw['events']) > arguments['limit']:
                    raise ValueError
                previous = arguments['after_sequence']
                for event in raw['events']:
                    keys = {'sequence', 'schema_version', 'kind', 'account_scope', 'journal_id', 'attempt_id', 'version', 'state', 'reason'}
                    if (type(event) is not dict or set(event) != keys
                            or type(event['schema_version']) is not int or event['schema_version'] != 1
                            or event['kind'] != 'SOURCE_GAP' or event['reason'] != 'FACT_DAY_SOURCE_UNAVAILABLE'
                            or event['account_scope'] != raw['account_scope']
                            or type(event['journal_id']) is not str or not event['journal_id']
                            or type(event['attempt_id']) is not str or not event['attempt_id']
                            or (event['state'], event['version']) not in {('DISPATCHING', 2), ('SENT', 3), ('NOT_SENT', 3), ('UNKNOWN', 3)}
                            or type(event['sequence']) is not int or event['sequence'] != previous + 1
                            or event['sequence'] > raw['offered_sequence']):
                        raise ValueError
                    previous = event['sequence']
                if (not raw['events'] and raw['has_more']) or (previous < raw['offered_sequence'] and not raw['has_more']):
                    raise ValueError
                if raw['events']:
                    if type(bound._binding_token) is not _BoundBindingToken:
                        raise ValueError
                    observation = bound.before
                    snapshot = BoundCallObservation(
                        WireIdentity(**vars(observation.identity)), observation.run_id,
                        type(observation.scope)(**vars(observation.scope)), observation.registration_id,
                        observation.service_epoch, observation.mount, observation.service_id,
                        observation.instance_id, observation.scope_ref, observation.operations,
                        WireIdentity(**vars(observation.peer)), observation.fingerprint,
                        observation.lease_deadline, observation.active, observation.required,
                        observation.operation)
                    self._read_binding = (observation, snapshot, raw['epoch'], previous,
                                          self._runtime, bound._binding_token)
                return CancelAttemptResult('OBSERVED', page=raw, binding=bound.before)
            except Exception:
                return CancelAttemptResult('UNKNOWN', code='INVALID_OR_UNAVAILABLE_REPLY')
        finally:
            self._lock.release()

    def read_after(self, *, after_sequence=0, expected_epoch=None, limit=256, timeout_seconds=5.0):
        if (not _sequence(after_sequence) or type(limit) is not int or not 1 <= limit <= 256
                or (expected_epoch is not None and not _epoch(expected_epoch)) or (after_sequence and expected_epoch is None)):
            raise ValueError('INVALID_READ')
        return self._call('account.cancel_attempts.read_after', {'after_sequence': after_sequence,
            'expected_epoch': expected_epoch, 'limit': limit}, timeout_seconds)

    def progress(self, *, timeout_seconds=5.0):
        return self._call('account.cancel_attempts.progress', {}, timeout_seconds)

    def acknowledge(self, sequence, *, expected_epoch, binding, timeout_seconds=5.0):
        if not _sequence(sequence) or not _epoch(expected_epoch) or type(binding) is not BoundCallObservation:
            raise ValueError('INVALID_ACK')
        return self._call('account.cancel_attempts.ack', {'sequence': sequence, 'expected_epoch': expected_epoch},
                          timeout_seconds, expected_binding=binding)
