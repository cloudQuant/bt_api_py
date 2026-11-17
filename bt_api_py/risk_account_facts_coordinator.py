"""Explicit durable account-facts observation; not trading authorization.

The caller owns the original strict client and Risk ledger. A single call
commits the candidate page before ACK; recovery is always an explicit call.
The source binding pins the service, instance, peer and key fingerprint.
Replacing an instance or rotating its key requires a future explicit source
transition; this coordinator does not automatically migrate the binding.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import threading
from dataclasses import dataclass
from functools import wraps

from bt_api_base.service_protocol import WireRole
from bt_api_risk.pretrade.ledger import PretradeLedger

from bt_api_py.gateway_account_facts_client import (
    GatewayAccountFactIssuedPage,
    GatewayAccountFactProgress,
    GatewayAccountFactResult,
    GatewayAccountFactsClient,
    GatewayAccountFactV2Result,
)
from bt_api_py.gateway_account_observer_client import GatewayAccountObserverClient
from bt_api_py.service_runtime_thread import BoundCallRoute, ThreadedServiceClientRuntime


@dataclass(frozen=True, slots=True)
class RiskAccountFactsOutcome:
    """Finite local outcome; none of these statuses proves account coverage."""

    status: str
    cursor: int | None = None
    code: str | None = None


def _exclusive(operation):
    @wraps(operation)
    def guarded(self):
        self._check_owner()
        if self._busy:
            self._poisoned = True
            raise RuntimeError('R05_FACTS_REENTRANT')
        self._busy = True
        try:
            return operation(self)
        finally:
            self._busy = False
    return guarded


class RiskAccountFactsCoordinator:
    """Fenced owner of one explicit read, local apply and ACK observation."""

    def __init__(self, client, ledger, *, limit=256, timeout_seconds=5.0):
        if (type(client) is not GatewayAccountFactsClient or type(client._runtime) is not ThreadedServiceClientRuntime
                or type(client._bound_route) is not BoundCallRoute
                or client._bound_route.peer.role is not WireRole.SERVICE
                or client._bound_route.peer.delegate_ref is not None
                or type(client._expected_fingerprint) is not str
                or re.fullmatch('[0-9a-f]{64}', client._expected_fingerprint) is None
                or (client._bound_route.identity, client._bound_route.scope, client._bound_route.scope_ref,
                    client._bound_route.service_id, client._bound_route.instance_id) != (
                        client._route.identity, client._route.scope, client._route.scope_ref,
                        client._route.service_id, client._route.instance_id)
                or type(ledger) is not PretradeLedger
                or ledger._schema_version != 4 or ledger._r05_enabled is not True or ledger._r05_version != 2
                or type(limit) is not int or not 1 <= limit <= 256
                or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError('R05_FACTS_COORDINATOR_INVALID')
        self._client, self._ledger = client, ledger
        self._runtime, self._journal, self._fence = client._runtime, ledger._journal, ledger._fence
        self._route, self._bound_route = client._route, client._bound_route
        self._route_value, self._bound_value = copy.deepcopy(self._route), copy.deepcopy(self._bound_route)
        self._fingerprint, self._consumer = client._expected_fingerprint, client._consumer_ref
        peer = self._bound_route.peer
        self._source_binding = {'service_id': self._bound_route.service_id,
            'instance_id': self._bound_route.instance_id, 'peer_role': peer.role.value,
            'peer_principal': peer.principal_ref, 'peer_actor': peer.actor_ref,
            'peer_delegate': peer.delegate_ref, 'fingerprint': self._fingerprint}
        self._fence_value = copy.deepcopy(self._fence)
        self._limit, self._timeout = limit, timeout_seconds
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._mode = 'READY'
        self._busy = False
        self._poisoned = False
        self._check()

    def _check(self):
        self._check_owner()
        c, ledger = self._client, self._ledger
        if (self._poisoned or c._runtime is not self._runtime or c._route is not self._route or c._route != self._route_value
                or c._bound_route is not self._bound_route or c._bound_route != self._bound_value
                or c._expected_fingerprint != self._fingerprint or c._consumer_ref != self._consumer
                or getattr(c._call, '__func__', None) is not GatewayAccountFactsClient._call
                or getattr(c._validate_issued, '__func__', None) is not GatewayAccountFactsClient._validate_issued
                or getattr(c._registration, '__func__', None) is not GatewayAccountObserverClient._registration
                or ledger._journal is not self._journal or ledger._fence is not self._fence
                or ledger._fence != self._fence_value or ledger._schema_version != 4
                or ledger._r05_enabled is not True or ledger._r05_version != 2):
            raise ValueError('R05_FACTS_BINDING_CHANGED')
        PretradeLedger._check(ledger)

    def _check_owner(self):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError('R05_FACTS_WRONG_OWNER')

    def _progress(self):
        self._check()
        with PretradeLedger.transaction(self._ledger) as txn:
            progress = txn.r05_progress()
            binding = txn.r05_source_binding()
            if binding is not None and binding != self._source_binding:
                raise ValueError('R05_FACTS_SOURCE_CHANGED')
            if progress is not None and (progress['gateway_account'] != self._route.scope.account_ref
                                         or progress['consumer_ref'] != self._consumer):
                raise ValueError('R05_FACTS_SOURCE_CHANGED')
        self._check()
        return progress

    def _latch_after_failure(self):
        try:
            state = self._progress()
            self._mode = 'RECOVER' if state is not None and state['pending'] is not None else 'UNKNOWN'
        except Exception:
            self._mode = 'UNKNOWN'

    def _issued_current(self, result):
        self._check()
        c = self._client
        issued, binding = c._issued_v2, c._read_binding
        if (issued is None or issued[0] is not result or binding is None or issued[2] is not binding[0]
                or issued[4] is not self._runtime or binding[6] is not self._runtime
                or binding[1] != binding[2] or c._registration('account.facts.read_after.v2') != issued[5]):
            raise ValueError('R05_FACTS_BINDING_CHANGED')

    @staticmethod
    def _page_wire(issued, after):
        if type(issued) is not GatewayAccountFactIssuedPage:
            raise ValueError('R05_FACTS_ISSUANCE_INVALID')
        page, progress = issued.page, issued.page.progress
        fields = ('sequence', 'fact_digest', 'disposition', 'reason', 'strategy_scope',
                  'execution_sequence', 'reservation_id', 'origin', 'parent_ref', 'child_id',
                  'external_receipt_digest', 'phase', 'event_digest')
        events = []
        for event in page.events:
            row = {name: getattr(event, name) for name in fields}
            row['observation'] = event.observation.to_mapping()
            events.append(row)
        value = {'gateway_account': progress.account_scope, 'gateway_journal_id': issued.journal_id,
                 'epoch': progress.epoch, 'consumer_ref': progress.consumer_ref, 'after_sequence': after,
                 'offered_sequence': progress.offered_sequence, 'acknowledged_sequence': progress.acknowledged_sequence,
                 'bootstrap_count': progress.bootstrap_count, 'has_more': page.has_more, 'events': events}
        # The only copy used afterwards is this detached immutable encoding.
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')

    def _read_page(self, *, after, epoch, limit):
        self._check()
        result = GatewayAccountFactsClient.read_after_v2(self._client, after_sequence=after, expected_epoch=epoch,
                                                        limit=limit, timeout_seconds=self._timeout)
        if type(result) is not GatewayAccountFactV2Result or result.outcome != 'OBSERVED':
            raise ValueError('R05_FACTS_READ_UNKNOWN')
        copy_page = GatewayAccountFactsClient.validate_copy_issued_page(self._client, result)
        encoded = self._page_wire(copy_page, after)
        del copy_page
        page = json.loads(encoded)
        if (page['gateway_account'] != self._route.scope.account_ref or page['consumer_ref'] != self._consumer
                or (epoch is not None and page['epoch'] != epoch)):
            raise ValueError('R05_FACTS_BINDING_CHANGED')
        self._issued_current(result)
        return result, encoded

    def _apply(self, result, encoded, before):
        self._issued_current(result)
        page = json.loads(encoded)
        page['events'] = tuple(page['events'])
        with PretradeLedger.transaction(self._ledger) as txn:
            if txn.r05_progress() != before:
                raise ValueError('R05_FACTS_PROGRESS_CHANGED')
            binding = txn.r05_source_binding()
            if binding is not None and binding != self._source_binding:
                raise ValueError('R05_FACTS_SOURCE_CHANGED')
            staged = txn.apply_r05_bound_fact_page(source_binding=dict(self._source_binding), **page)
        self._issued_current(result)
        if self._progress() != staged:
            raise ValueError('R05_FACTS_PROGRESS_CHANGED')
        return staged

    def _record_ack(self, epoch, sequence, expected):
        self._check()
        with PretradeLedger.transaction(self._ledger) as txn:
            if txn.r05_progress() != expected:
                raise ValueError('R05_FACTS_PROGRESS_CHANGED')
            if txn.r05_source_binding() != self._source_binding:
                raise ValueError('R05_FACTS_SOURCE_CHANGED')
            final = txn.record_r05_bound_ack_observation(source_binding=dict(self._source_binding),
                                                         epoch=epoch, sequence=sequence)
        self._check()
        if self._progress() != final or final['pending'] is not None or final['ack_observed'] != sequence:
            raise ValueError('R05_FACTS_PROGRESS_CHANGED')
        return final

    def _ack(self, result, page, staged):
        if self._progress() != staged:
            raise ValueError('R05_FACTS_PROGRESS_CHANGED')
        self._issued_current(result)
        registration = self._client._issued_v2[5]
        sequence, epoch = staged['pending'], staged['epoch']
        reply = GatewayAccountFactsClient.acknowledge(self._client, sequence, expected_epoch=epoch,
            timeout_seconds=self._timeout, read_token=result._read_token)
        if (type(reply) is not GatewayAccountFactResult or reply.outcome != 'OBSERVED'
                or type(reply.progress) is not GatewayAccountFactProgress
                or reply.progress.account_scope != page['gateway_account']
                or reply.progress.consumer_ref != page['consumer_ref'] or reply.progress.epoch != epoch
                or reply.progress.bootstrap_count != page['bootstrap_count']
                or reply.progress.acknowledged_sequence != sequence
                or reply.progress.offered_sequence < sequence
                or self._client._registration('account.facts.ack') != registration):
            raise ValueError('R05_FACTS_ACK_UNKNOWN')
        final = self._record_ack(epoch, sequence, staged)
        self._check()
        if self._client._registration('account.facts.ack') != registration:
            raise ValueError('R05_FACTS_BINDING_CHANGED')
        return final

    @_exclusive
    def poll_once(self):
        self._check_owner()
        try:
            self._check()
        except Exception:
            self._mode = 'UNKNOWN'
            return RiskAccountFactsOutcome('UNKNOWN', code='OWNER_UNCERTAIN')
        try:
            before = self._progress()
        except Exception:
            self._mode = 'UNKNOWN'
            return RiskAccountFactsOutcome('UNKNOWN', code='LOCAL_UNAVAILABLE')
        if before is not None and before['pending'] is not None:
            self._mode = 'RECOVER'
            return RiskAccountFactsOutcome('RECOVERY_REQUIRED', before['cursor'])
        if self._mode != 'READY':
            return RiskAccountFactsOutcome('UNKNOWN', code='OWNER_UNCERTAIN')
        after = 0 if before is None else before['cursor']
        epoch = None if before is None else before['epoch']
        try:
            result, encoded = self._read_page(after=after, epoch=epoch, limit=self._limit)
            page = json.loads(encoded)
            if page['acknowledged_sequence'] != after:
                raise ValueError('R05_FACTS_ACK_DRIFT')
            staged = self._apply(result, encoded, before)
            if not page['events']:
                return RiskAccountFactsOutcome('EMPTY', staged['cursor'])
        except Exception:
            self._mode = 'UNKNOWN'
            return RiskAccountFactsOutcome('UNKNOWN', code='READ_OR_APPLY_UNAVAILABLE')
        try:
            final = self._ack(result, page, staged)
        except Exception:
            self._latch_after_failure()
            return RiskAccountFactsOutcome('UNKNOWN', staged['cursor'], 'ACK_OR_COMMIT_UNAVAILABLE')
        self._mode = 'READY'
        return RiskAccountFactsOutcome('APPLIED', final['cursor'])

    @_exclusive
    def recover_pending_once(self):
        """Explicitly re-read exactly the original pending range, once."""
        self._check_owner()
        try:
            self._check()
        except Exception:
            self._mode = 'UNKNOWN'
            return RiskAccountFactsOutcome('UNKNOWN', code='OWNER_UNCERTAIN')
        try:
            before = self._progress()
            if before is None or before['pending'] is None:
                return (RiskAccountFactsOutcome('NO_PENDING') if self._mode != 'UNKNOWN'
                        else RiskAccountFactsOutcome('UNKNOWN', code='OWNER_UNCERTAIN'))
            start, end = before['ack_observed'], before['pending']
            if not 1 <= end - start <= 256:
                raise ValueError('R05_FACTS_RECOVERY_RANGE_INVALID')
            result, encoded = self._read_page(after=start, epoch=before['epoch'], limit=end - start)
            page = json.loads(encoded)
            if (len(page['events']) != end - start or page['acknowledged_sequence'] not in (start, end)
                    or page['gateway_journal_id'] != before['gateway_journal_id']):
                raise ValueError('R05_FACTS_RECOVERY_CHANGED')
            staged = self._apply(result, encoded, before)
            if staged != before:
                raise ValueError('R05_FACTS_PROGRESS_CHANGED')
            if page['acknowledged_sequence'] == end:
                self._issued_current(result)
                final = self._record_ack(before['epoch'], end, staged)
                self._issued_current(result)
            else:
                final = self._ack(result, page, staged)
        except Exception:
            self._latch_after_failure()
            return RiskAccountFactsOutcome('UNKNOWN', code='RECOVERY_UNAVAILABLE')
        self._mode = 'READY'
        return RiskAccountFactsOutcome('APPLIED', final['cursor'])
