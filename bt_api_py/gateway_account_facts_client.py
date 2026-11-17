"""Authenticated account facts stream; consumers durably apply before ACK.

This cursor and epoch are independent of account.events. BOOTSTRAP events are
current snapshots at migration, never a claim of prior stream completeness.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from dataclasses import dataclass, field, replace

from bt_api_base.gateway_execution_stream_protocol import GatewayExecutionObservation
from bt_api_base.service_protocol import WireIdentity, WireScope

from bt_api_py.gateway_account_facts_identity import account_facts_consumer_ref
from bt_api_py.gateway_account_observer_client import (
    GatewayAccountObserverClient,
    GatewayAccountObserverRoute,
)
from bt_api_py.gateway_delegate_service_client import runtime_failure
from bt_api_py.gateway_execution_stream_client import _detach_wire, _epoch, _sequence
from bt_api_py.service_runtime import ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import (
    BoundCallObservation,
    BoundCallResult,
    _BoundBindingToken,
)


class _FactReadToken:
    """Process-local, single-use identity; never a serializable receipt."""

    __slots__ = ()

    def __copy__(self):
        raise TypeError('READ_TOKEN_NOT_COPYABLE')

    def __deepcopy__(self, _memo):
        raise TypeError('READ_TOKEN_NOT_COPYABLE')

    def __reduce__(self):
        raise TypeError('READ_TOKEN_NOT_SERIALIZABLE')

    def __reduce_ex__(self, _protocol):
        raise TypeError('READ_TOKEN_NOT_SERIALIZABLE')


@dataclass(frozen=True, slots=True)
class GatewayAccountFact:
    sequence: int
    fact_digest: str
    observation: GatewayExecutionObservation
    disposition: str
    reason: str
    strategy_scope: str | None
    execution_sequence: int | None
    reservation_id: str | None
    origin: str | None
    parent_ref: str | None
    child_id: str | None
    external_receipt_digest: str | None
    phase: str
    event_digest: str


@dataclass(frozen=True, slots=True)
class GatewayAccountFactProgress:
    account_scope: str
    consumer_ref: str
    epoch: str
    offered_sequence: int
    acknowledged_sequence: int
    bootstrap_count: int


@dataclass(frozen=True, slots=True)
class GatewayAccountFactPage:
    progress: GatewayAccountFactProgress
    events: tuple[GatewayAccountFact, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class GatewayAccountFactStatus:
    status: str
    unattributed: int | None
    conflict: int | None
    verified_external: int | None


@dataclass(frozen=True, slots=True)
class GatewayAccountFactSnapshotInfo:
    account_scope: str
    consumer_ref: str
    epoch: str
    high_sequence: int
    retained_floor: int
    snapshot_id: str | None
    snapshot_digest: str | None
    snapshot_count: int


@dataclass(frozen=True, slots=True)
class GatewayAccountFactSnapshot:
    account_scope: str
    consumer_ref: str
    epoch: str
    snapshot_id: str
    snapshot_digest: str
    watermark: int
    total: int
    after_ordinal: int
    events: tuple[GatewayAccountFact, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class GatewayAccountFactResult:
    outcome: str
    page: GatewayAccountFactPage | None = None
    progress: GatewayAccountFactProgress | None = None
    code: str | None = None
    fact_status: GatewayAccountFactStatus | None = None
    snapshot_info: GatewayAccountFactSnapshotInfo | None = None
    snapshot: GatewayAccountFactSnapshot | None = None
    _read_token: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class GatewayAccountFactV2Result:
    outcome: str
    journal_id: str | None = None
    page: GatewayAccountFactPage | None = None
    code: str | None = None
    _read_token: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class GatewayAccountFactIssuedPage:
    """Detached authenticated observation, not a ledger or trading authorization."""
    journal_id: str
    page: GatewayAccountFactPage


def _issued_wire(result):
    page = result.page
    progress = page.progress
    raw = {name: getattr(progress, name) for name in (
        'account_scope', 'consumer_ref', 'epoch', 'offered_sequence',
        'acknowledged_sequence', 'bootstrap_count')}
    raw.update(schema_version=1, has_more=page.has_more)
    fields = ('sequence', 'fact_digest', 'disposition', 'reason', 'strategy_scope',
              'execution_sequence', 'reservation_id', 'origin', 'parent_ref', 'child_id',
              'external_receipt_digest', 'phase', 'event_digest')
    raw['events'] = []
    for event in page.events:
        item = {name: getattr(event, name) for name in fields}
        item['observation'] = event.observation.to_mapping()
        raw['events'].append(item)
    return {'status': 'OK', 'schema': 'sdk.gateway.account_facts.page/2',
            'journal_id': result.journal_id, 'page': raw}


def _issued_bytes(result):
    return json.dumps(_issued_wire(result), sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def _issued_digest(result):
    return hashlib.sha256(_issued_bytes(result)).hexdigest()


def _text(value):
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 256


def _parse_fact(item, *, account, maximum, bootstrap_count=None):
    keys = {'sequence', 'fact_digest', 'observation', 'disposition', 'reason', 'strategy_scope',
            'execution_sequence', 'reservation_id', 'origin', 'parent_ref', 'child_id',
            'external_receipt_digest', 'phase', 'event_digest'}
    if type(item) is not dict or set(item) != keys:
        raise ValueError
    observation = GatewayExecutionObservation.from_mapping(item['observation'])
    strategy, execution_seq, receipt = item['strategy_scope'], item['execution_sequence'], item['external_receipt_digest']
    disposition = item['disposition']
    reservation, origin, parent, child = item['reservation_id'], item['origin'], item['parent_ref'], item['child_id']
    if (not _sequence(item['sequence']) or item['sequence'] == 0 or item['sequence'] > maximum
            or not _epoch(item['fact_digest']) or not _epoch(item['event_digest'])
            or observation.account_scope != account or not _text(item['reason'])
            or disposition not in {'ATTRIBUTED', 'UNATTRIBUTED', 'CONFLICT', 'VERIFIED_EXTERNAL'}
            or item['phase'] not in {'BOOTSTRAP', 'LIVE'}
            or (bootstrap_count is not None and item['phase'] !=
                ('BOOTSTRAP' if item['sequence'] <= bootstrap_count else 'LIVE'))
            or (reservation is None) != (origin is None) or (reservation is not None and not _text(reservation))
            or (origin is not None and (not _text(origin) or (origin != 'self' and
                (not (origin.startswith('risk:') or origin.startswith('execution:'))
                 or not _text(origin.partition(':')[2])))))
            or (parent is None) != (child is None)
            or (parent is not None and (not _text(parent) or not _text(child) or origin is None))
            or (origin == 'self' and (parent is not None or child is not None))
            or (origin is not None and origin.startswith('execution:') and parent is None)
            or (disposition != 'ATTRIBUTED' and any(v is not None for v in (reservation, origin, parent, child)))
            or (disposition == 'ATTRIBUTED' and (not _text(strategy)
                or (execution_seq is not None and (not _sequence(execution_seq) or execution_seq == 0))
                or (execution_seq is None and item['reason'] != 'TRADE_DUPLICATE') or receipt is not None))
            or (disposition == 'VERIFIED_EXTERNAL' and (strategy is not None or execution_seq is not None
                or not _epoch(receipt)))
            or (disposition in {'UNATTRIBUTED', 'CONFLICT'} and
                (strategy is not None or execution_seq is not None or receipt is not None))):
        raise ValueError
    return GatewayAccountFact(item['sequence'], item['fact_digest'], observation, disposition, item['reason'],
        strategy, execution_seq, reservation, origin, parent, child, receipt, item['phase'], item['event_digest'])


class GatewayAccountFactsClient(GatewayAccountObserverClient):
    """Read account facts; an authenticated page token gates explicit ACK.

    In strict mode a later read invalidates the previous token, and each ACK
    consumes it before sending. The token binds one local observation; it is
    not proof that the caller committed a transaction or of cross-store atomicity.
    Strict calls require a registration on a strict single-connection transport
    and retain its local socket lineage. This is not proof of account truth.
    An UNKNOWN ACK is never retried automatically; an explicit authenticated
    reread may issue a new token for an idempotent ACK of the same page.
    Legacy unbound mode has no authenticated read token or source proof.
    """

    def __init__(self, runtime, *, route: GatewayAccountObserverRoute,
                 peer: WireIdentity | None = None, fingerprint: str | None = None):
        super().__init__(runtime, route=route, peer=peer, fingerprint=fingerprint)
        self._issued_v2 = None
        identity, scope = route.identity, route.scope
        self._consumer_ref = account_facts_consumer_ref(principal_ref=identity.principal_ref, identity=identity, scope=scope)

    def _call(self, operation, arguments, timeout_seconds, *, read_token=None):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError('ACCOUNT_FACTS_WRONG_OWNER')
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError('INVALID_TIMEOUT')
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('ACCOUNT_FACTS_BUSY')
        try:
            issued = None
            if self._bound_route is not None:
                if operation in {'account.facts.read_after', 'account.facts.read_after.v2'}:
                    self._read_binding = None
                    self._issued_v2 = None
                elif operation == 'account.facts.ack':
                    issued = self._read_binding
                    if (issued is None or read_token is not issued[0] or issued[1] != issued[2]
                            or arguments['expected_epoch'] != issued[3]
                            or arguments['sequence'] != issued[5] or self._runtime is not issued[6]):
                        return GatewayAccountFactResult('TRANSPORT_NOT_STARTED', code='READ_BINDING_REQUIRED')
                    if self._issued_v2 is not None:
                        try:
                            self._validate_issued(self._issued_v2[0])
                        except Exception:
                            self._read_binding = None
                            self._issued_v2 = None
                            return GatewayAccountFactResult('TRANSPORT_NOT_STARTED', code='READ_BINDING_REQUIRED')
                    if issued[5] <= issued[4]:
                        return GatewayAccountFactResult('TRANSPORT_NOT_STARTED', code='READ_BINDING_REQUIRED')
                    self._read_binding = None
                    self._issued_v2 = None
            try:
                registration = self._registration(operation)
            except Exception:
                return GatewayAccountFactResult('TRANSPORT_NOT_STARTED', code='ROUTE_UNAVAILABLE')
            try:
                if self._bound_route is None:
                    result = _detach_wire(self._runtime.call(self._route.mount, operation, arguments,
                        account_ref=self._route.scope.account_ref, scope_ref=self._route.scope_ref,
                        timeout_seconds=timeout_seconds))
                    bound = None
                else:
                    kwargs = {'route': self._bound_route, 'timeout_seconds': timeout_seconds,
                              'expected_server_key_fingerprint': self._expected_fingerprint}
                    if issued is not None:
                        kwargs.update(expected_binding=issued[1], _expected_token=issued[7])
                    bound = self._runtime.call_bound(self._route.mount, operation, arguments, **kwargs)
                    result = _detach_wire(bound.result) if type(bound) is BoundCallResult else None
            except ServiceRuntimeRequestNotSentError:
                return GatewayAccountFactResult('TRANSPORT_NOT_STARTED', code='REQUEST_NOT_SENT')
            except Exception as error:
                if self._bound_route is None:
                    return GatewayAccountFactResult('UNKNOWN', code='RPC_OUTCOME_UNKNOWN')
                outcome, code = runtime_failure(error)
                return GatewayAccountFactResult(outcome, code=code)
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
                        'CONSUMER_CAPACITY_EXCEEDED', 'ACCOUNT_FACT_STREAM_UNAVAILABLE',
                        'CONSUMER_REPLAY_REQUIRED', 'RECOVERY_REQUIRED', 'SNAPSHOT_REPLACED',
                        'SNAPSHOT_NOT_OFFERED'}:
                        raise ValueError
                    return GatewayAccountFactResult('REQUEST_DENIED', code=result['code'])
                if operation in {'account.facts.snapshot_info', 'account.facts.snapshot_confirm'}:
                    if set(result) != {'status', 'snapshot_info'} or result['status'] != 'OK':
                        raise ValueError
                    value = result['snapshot_info']
                    if value is None and operation == 'account.facts.snapshot_info':
                        return GatewayAccountFactResult('OBSERVED')
                    keys = {'schema_version', 'account_scope', 'consumer_ref', 'epoch', 'high_sequence',
                            'retained_floor', 'snapshot_id', 'snapshot_digest', 'snapshot_count'}
                    if (type(value) is not dict or set(value) != keys or type(value['schema_version']) is not int
                            or value['schema_version'] != 1
                            or value['account_scope'] != self._route.scope.account_ref
                            or value['consumer_ref'] != self._consumer_ref or not _epoch(value['epoch'])
                            or not _sequence(value['high_sequence']) or not _sequence(value['retained_floor'])
                            or not _sequence(value['snapshot_count']) or value['retained_floor'] > value['high_sequence']
                            or ((value['snapshot_id'] is None or value['snapshot_digest'] is None) and
                                (value['snapshot_id'] is not None or value['snapshot_digest'] is not None
                                 or value['snapshot_count'] != 0 or value['retained_floor'] != 0))
                            or (value['snapshot_id'] is not None and
                                (not _epoch(value['snapshot_id']) or not _epoch(value['snapshot_digest'])))
                            or (operation == 'account.facts.snapshot_confirm' and
                                (value['snapshot_id'] != arguments['snapshot_id'] or
                                 value['snapshot_digest'] != arguments['snapshot_digest']))):
                        raise ValueError
                    return GatewayAccountFactResult('OBSERVED', snapshot_info=GatewayAccountFactSnapshotInfo(
                        *(value[k] for k in ('account_scope', 'consumer_ref', 'epoch', 'high_sequence',
                          'retained_floor', 'snapshot_id', 'snapshot_digest', 'snapshot_count'))))
                if operation == 'account.facts.snapshot_read':
                    if set(result) != {'status', 'snapshot'} or result['status'] != 'OK' or type(result['snapshot']) is not dict:
                        raise ValueError
                    value = result['snapshot']
                    keys = {'schema_version', 'account_scope', 'consumer_ref', 'epoch', 'snapshot_id',
                            'snapshot_digest', 'watermark', 'total', 'after_ordinal', 'events', 'has_more'}
                    if (set(value) != keys or type(value['schema_version']) is not int or value['schema_version'] != 1
                            or value['account_scope'] != self._route.scope.account_ref
                            or value['consumer_ref'] != self._consumer_ref or not _epoch(value['epoch'])
                            or value['snapshot_id'] != arguments['snapshot_id'] or not _epoch(value['snapshot_digest'])
                            or not _sequence(value['watermark']) or not _sequence(value['total'])
                            or not _sequence(value['after_ordinal'])
                            or value['after_ordinal'] != arguments['after_ordinal'] or type(value['events']) is not list
                            or len(value['events']) > arguments['limit'] or type(value['has_more']) is not bool
                            or value['after_ordinal'] + len(value['events']) > value['total']
                            or value['has_more'] != (value['after_ordinal'] + len(value['events']) < value['total'])
                            or (not value['events'] and value['has_more'])):
                        raise ValueError
                    events = tuple(_parse_fact(item, account=value['account_scope'], maximum=value['watermark'])
                                   for item in value['events'])
                    if len({item.fact_digest for item in events}) != len(events):
                        raise ValueError
                    return GatewayAccountFactResult('OBSERVED', snapshot=GatewayAccountFactSnapshot(
                        value['account_scope'], value['consumer_ref'], value['epoch'], value['snapshot_id'],
                        value['snapshot_digest'], value['watermark'], value['total'], value['after_ordinal'],
                        events, value['has_more']))
                if operation == 'account.facts.status':
                    if set(result) != {'status', 'fact_status'} or result['status'] != 'OK':
                        raise ValueError
                    value = result['fact_status']
                    keys = {'status', 'unattributed', 'conflict', 'verified_external'}
                    if (type(value) is not dict or set(value) != keys
                            or value['status'] not in {'UNAVAILABLE', 'CONFLICT', 'INCOMPLETE',
                                                       'NO_UNRESOLVED_OBSERVED'}
                            or (value['status'] == 'UNAVAILABLE' and any(value[k] is not None
                                for k in keys - {'status'}))
                            or (value['status'] != 'UNAVAILABLE' and any(type(value[k]) is not int
                                or value[k] < 0 for k in keys - {'status'}))
                            or (value['status'] == 'CONFLICT' and not value['conflict'])
                            or (value['status'] == 'INCOMPLETE' and (value['conflict'] or not value['unattributed']))
                            or (value['status'] == 'NO_UNRESOLVED_OBSERVED' and
                                (value['conflict'] or value['unattributed']))):
                        raise ValueError
                    return GatewayAccountFactResult('OBSERVED', fact_status=GatewayAccountFactStatus(
                        value['status'], value['unattributed'], value['conflict'], value['verified_external']))
                version2 = operation == 'account.facts.read_after.v2'
                field = 'page' if operation in {'account.facts.read_after', 'account.facts.read_after.v2'} else 'progress'
                expected_fields = {'status', field} | ({'schema', 'journal_id'} if version2 else set())
                if (set(result) != expected_fields or result['status'] != 'OK' or type(result[field]) is not dict
                        or (version2 and (result['schema'] != 'sdk.gateway.account_facts.page/2'
                                          or not _text(result['journal_id'])))):
                    raise ValueError
                raw = result[field]
                fields = {'schema_version', 'account_scope', 'consumer_ref', 'epoch',
                          'offered_sequence', 'acknowledged_sequence', 'bootstrap_count'}
                if field == 'page':
                    fields |= {'has_more', 'events'}
                if (set(raw) != fields or type(raw['schema_version']) is not int or raw['schema_version'] != 1
                        or raw['account_scope'] != self._route.scope.account_ref or raw['consumer_ref'] != self._consumer_ref
                        or not _epoch(raw['epoch']) or not _sequence(raw['offered_sequence'])
                        or not _sequence(raw['acknowledged_sequence']) or not _sequence(raw['bootstrap_count'])
                        or raw['acknowledged_sequence'] > raw['offered_sequence']
                        or (arguments.get('expected_epoch') is not None and raw['epoch'] != arguments['expected_epoch'])):
                    raise ValueError
                progress = GatewayAccountFactProgress(*(raw[key] for key in ('account_scope', 'consumer_ref',
                    'epoch', 'offered_sequence', 'acknowledged_sequence', 'bootstrap_count')))
                if field == 'progress':
                    if operation == 'account.facts.ack' and progress.acknowledged_sequence < arguments['sequence']:
                        raise ValueError
                    return GatewayAccountFactResult('OBSERVED', progress=progress)
                if (type(raw['has_more']) is not bool or type(raw['events']) is not list
                        or len(raw['events']) > arguments['limit'] or (not raw['events'] and raw['has_more'])):
                    raise ValueError
                events = []
                previous = arguments['after_sequence']
                for item in raw['events']:
                    event = _parse_fact(item, account=progress.account_scope, maximum=progress.offered_sequence,
                                        bootstrap_count=progress.bootstrap_count)
                    if event.sequence != previous + 1:
                        raise ValueError
                    previous = event.sequence
                    events.append(event)
                if ((not events and previous < progress.bootstrap_count)
                        or (previous < progress.offered_sequence and not raw['has_more'])):
                    raise ValueError
                token = None
                if bound is not None and (events or version2):
                    if type(bound._binding_token) is not _BoundBindingToken:
                        raise ValueError
                    token = _FactReadToken()
                    snapshot = replace(bound.before, identity=WireIdentity(**vars(bound.before.identity)),
                                       scope=WireScope(**vars(bound.before.scope)),
                                       peer=WireIdentity(**vars(bound.before.peer)))
                    digest = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':'),
                                                       ensure_ascii=True, allow_nan=False).encode('ascii')).hexdigest()
                    self._read_binding = (token, bound.before, snapshot, raw['epoch'],
                                          arguments['after_sequence'], previous, self._runtime,
                                          bound._binding_token, digest)
                page = GatewayAccountFactPage(progress, tuple(events), raw['has_more'])
                if version2:
                    if token is None:
                        raise ValueError
                    issued_result = GatewayAccountFactV2Result('OBSERVED', result['journal_id'], page,
                                                               _read_token=token)
                    if _issued_digest(issued_result) != digest:
                        raise ValueError
                    identities = (page.progress, page.events,
                                  tuple((event, event.observation) for event in page.events))
                    self._issued_v2 = (issued_result, page, token, digest, self._runtime, registration, identities)
                    return issued_result
                return GatewayAccountFactResult('OBSERVED', page=page, _read_token=token)
            except Exception:
                return GatewayAccountFactResult('UNKNOWN', code='INVALID_OR_UNAVAILABLE_REPLY')
        finally:
            self._lock.release()

    def read_after(self, *, after_sequence=0, expected_epoch=None, limit=256, timeout_seconds=5.0):
        if (not _sequence(after_sequence) or type(limit) is not int or not 1 <= limit <= 256
                or (expected_epoch is not None and not _epoch(expected_epoch)) or (after_sequence and expected_epoch is None)):
            raise ValueError('INVALID_READ')
        return self._call('account.facts.read_after', {'after_sequence': after_sequence,
            'expected_epoch': expected_epoch, 'limit': limit}, timeout_seconds)

    def read_after_v2(self, *, after_sequence=0, expected_epoch=None, limit=256, timeout_seconds=5.0):
        """Read journal-bound facts using only the authenticated strict transport."""
        if self._bound_route is None:
            raise ValueError('STRICT_ACCOUNT_FACTS_REQUIRED')
        if (not _sequence(after_sequence) or type(limit) is not int or not 1 <= limit <= 256
                or (expected_epoch is not None and not _epoch(expected_epoch))
                or (after_sequence and expected_epoch is None)):
            raise ValueError('INVALID_READ')
        result = self._call('account.facts.read_after.v2', {'after_sequence': after_sequence,
            'expected_epoch': expected_epoch, 'limit': limit}, timeout_seconds)
        if type(result) is GatewayAccountFactV2Result:
            return result
        return GatewayAccountFactV2Result(result.outcome, code=result.code)

    def _validate_issued(self, result):
        issued = self._issued_v2
        if (issued is None or result is not issued[0] or type(result) is not GatewayAccountFactV2Result
                or result.page is not issued[1] or result._read_token is not issued[2]
                or self._runtime is not issued[4] or result.outcome != 'OBSERVED' or result.code is not None
                or self._read_binding is None or self._read_binding[0] is not issued[2]
                or self._read_binding[1] != self._read_binding[2]
                or self._read_binding[6] is not issued[4] or self._read_binding[8] != issued[3]
                or type(result.page) is not GatewayAccountFactPage
                or result.page.progress is not issued[6][0] or result.page.events is not issued[6][1]
                or type(result.page.events) is not tuple
                or len(result.page.events) != len(issued[6][2])
                or any(event is not original or event.observation is not observation
                       for event, (original, observation) in zip(result.page.events, issued[6][2]))
                or self._registration('account.facts.read_after.v2') != issued[5]):
            raise ValueError('ISSUED_PAGE_INVALID')
        # Once encoded, this immutable snapshot is the only data checked and
        # returned. DTOs can be force-mutated by another thread despite frozen
        # dataclasses, and the client lock does not protect those objects.
        snapshot = _issued_bytes(result)
        if hashlib.sha256(snapshot).hexdigest() != issued[3]:
            raise ValueError('ISSUED_PAGE_INVALID')
        return json.loads(snapshot)

    def validate_copy_issued_page(self, result):
        """Copy the exact issued response on its creating thread; no commit is implied.

        A later read or any ACK consumes the issuance. ACK independently fences
        the original socket; this copy does not guarantee a continuing connection.
        """
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError('ACCOUNT_FACTS_WRONG_OWNER')
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('ACCOUNT_FACTS_BUSY')
        try:
            wire = self._validate_issued(result)
            raw = wire['page']
            progress = GatewayAccountFactProgress(*(raw[k] for k in ('account_scope', 'consumer_ref',
                'epoch', 'offered_sequence', 'acknowledged_sequence', 'bootstrap_count')))
            events = tuple(_parse_fact(item, account=progress.account_scope, maximum=progress.offered_sequence,
                                       bootstrap_count=progress.bootstrap_count) for item in raw['events'])
            return GatewayAccountFactIssuedPage(wire['journal_id'],
                GatewayAccountFactPage(progress, events, raw['has_more']))
        except Exception as error:
            raise ValueError('ISSUED_PAGE_INVALID') from error
        finally:
            self._lock.release()

    def snapshot_info(self, *, timeout_seconds=5.0):
        """Read Gateway retention metadata; it is not an exchange snapshot."""
        return self._call('account.facts.snapshot_info', {}, timeout_seconds)

    def read_snapshot(self, snapshot_id, *, after_ordinal=0, limit=256, timeout_seconds=5.0):
        if not _epoch(snapshot_id) or not _sequence(after_ordinal) or type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError('INVALID_SNAPSHOT_READ')
        return self._call('account.facts.snapshot_read', {'snapshot_id': snapshot_id,
            'after_ordinal': after_ordinal, 'limit': limit}, timeout_seconds)

    def confirm_snapshot(self, snapshot_id, snapshot_digest, *, timeout_seconds=5.0):
        """Call only after durably installing every page of this exact snapshot."""
        if not _epoch(snapshot_id) or not _epoch(snapshot_digest):
            raise ValueError('INVALID_SNAPSHOT_CONFIRM')
        return self._call('account.facts.snapshot_confirm', {'snapshot_id': snapshot_id,
            'snapshot_digest': snapshot_digest}, timeout_seconds)

    def status(self, *, timeout_seconds=5.0):
        return self._call('account.facts.status', {}, timeout_seconds)

    def progress(self, *, timeout_seconds=5.0):
        return self._call('account.facts.progress', {}, timeout_seconds)

    def acknowledge(self, sequence, *, expected_epoch, timeout_seconds=5.0, read_token=None):
        if not _sequence(sequence) or not _epoch(expected_epoch):
            raise ValueError('INVALID_ACK')
        return self._call('account.facts.ack', {'sequence': sequence, 'expected_epoch': expected_epoch}, timeout_seconds,
                          read_token=read_token)
