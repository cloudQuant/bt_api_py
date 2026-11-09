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
from dataclasses import dataclass

from bt_api_base.gateway_execution_stream_protocol import GatewayExecutionObservation
from bt_api_py.gateway_account_observer_client import GatewayAccountObserverClient, GatewayAccountObserverRoute
from bt_api_py.gateway_execution_stream_client import _detach_wire, _epoch, _sequence
from bt_api_py.service_runtime import ServiceRuntimeRequestNotSentError


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
    """READ-only access using explicitly granted account.facts operations."""

    def __init__(self, runtime, *, route: GatewayAccountObserverRoute):
        super().__init__(runtime, route=route)
        identity, scope = route.identity, route.scope
        self._consumer_ref = 'account-facts:' + hashlib.sha256(json.dumps([
            identity.principal_ref, identity.role.value, identity.actor_ref, scope.execution_domain_ref,
            scope.account_ref, scope.strategy_ref], separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()

    def _call(self, operation, arguments, timeout_seconds):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError('ACCOUNT_FACTS_WRONG_OWNER')
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError('INVALID_TIMEOUT')
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('ACCOUNT_FACTS_BUSY')
        try:
            try:
                registration = self._registration(operation)
            except Exception:
                return GatewayAccountFactResult('TRANSPORT_NOT_STARTED', code='ROUTE_UNAVAILABLE')
            try:
                result = _detach_wire(self._runtime.call(self._route.mount, operation, arguments,
                    account_ref=self._route.scope.account_ref, scope_ref=self._route.scope_ref,
                    timeout_seconds=timeout_seconds))
            except ServiceRuntimeRequestNotSentError:
                return GatewayAccountFactResult('TRANSPORT_NOT_STARTED', code='REQUEST_NOT_SENT')
            except Exception:
                return GatewayAccountFactResult('UNKNOWN', code='RPC_OUTCOME_UNKNOWN')
            try:
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
                field = 'page' if operation == 'account.facts.read_after' else 'progress'
                if set(result) != {'status', field} or result['status'] != 'OK' or type(result[field]) is not dict:
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
                return GatewayAccountFactResult('OBSERVED', page=GatewayAccountFactPage(progress, tuple(events), raw['has_more']))
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

    def acknowledge(self, sequence, *, expected_epoch, timeout_seconds=5.0):
        if not _sequence(sequence) or not _epoch(expected_epoch):
            raise ValueError('INVALID_ACK')
        return self._call('account.facts.ack', {'sequence': sequence, 'expected_epoch': expected_epoch}, timeout_seconds)
