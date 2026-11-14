"""Bound, audited single-parent local observations, never trading authority.

The service authorizes and audits each query. The client's local result time
is a wall-clock observation, not a source clock or a freshness guarantee. On
macOS the runtime's timeout and lease clock does not count machine sleep; a
reply read after wake may therefore no longer be fresh. No result is cached.
"""
from __future__ import annotations

import math
import os
import re
import threading
import time
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py.execution_management_read_grant import ExecutionManagementReadRequest, PARENT_READ_V1, _reference
from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.service_runtime import BoundExecutionManagementReadTarget
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime


@dataclass(frozen=True, slots=True)
class ExecutionManagementParentSnapshot:
    """Local journal contents at the service's audited read point."""

    journal_incarnation: str
    parent_ref: str
    instrument: MarketInstrumentKey
    side: str
    offset: str
    quantity: str
    quantity_unit: str
    limit_price: str
    time_in_force: str
    expires_at_ns: int
    created_at_ns: int
    updated_at_ns: int
    intake_local_state: str
    risk_selected: bool
    risk_local_state: str
    gateway_local_state: str
    cancel_local_state: str


@dataclass(frozen=True, slots=True)
class ExecutionManagementReadObservation:
    status: str
    source: str
    local_result_time_ns: int
    snapshot: ExecutionManagementParentSnapshot | None = None


_FIELDS = frozenset({'status', 'source', 'journal_incarnation', 'parent_ref', 'instrument',
    'side', 'offset', 'quantity', 'quantity_unit', 'limit_price', 'time_in_force', 'expires_at_ns',
    'created_at_ns', 'updated_at_ns', 'intake_local_state', 'risk_selected', 'risk_local_state',
    'gateway_local_state', 'cancel_local_state'})


def _observation(snapshot=None):
    return ExecutionManagementReadObservation('UNKNOWN' if snapshot is None else 'OBSERVED',
                                               'LOCAL_JOURNAL', time.time_ns(), snapshot)


def _positive_decimal(value):
    if type(value) is not str or re.fullmatch(r'[0-9]{1,42}(?:\.[0-9]{1,42})?', value) is None:
        return False
    try:
        number = Decimal(value)
        return number.is_finite() and number > 0
    except (InvalidOperation, ValueError, OverflowError):
        return False


def _parse_snapshot(raw, parent_ref):
    if (type(raw) is not dict or set(raw) != _FIELDS or raw['source'] != 'LOCAL_JOURNAL'):
        raise ValueError
    if raw['status'] == 'UNKNOWN':
        if any(raw[key] is not None for key in _FIELDS - {'status', 'source'}):
            raise ValueError
        return None
    incarnation = raw['journal_incarnation']
    if (raw['status'] != 'OBSERVED' or raw['parent_ref'] != parent_ref
            or type(incarnation) is not str or len(incarnation) != 32
            or any(c not in '0123456789abcdef' for c in incarnation)
            or raw['side'] not in ('buy', 'sell')
            or raw['offset'] not in ('open', 'close', 'close_today', 'close_yesterday')
            or not _positive_decimal(raw['quantity']) or not _positive_decimal(raw['limit_price'])
            or not _reference(raw['quantity_unit']) or raw['time_in_force'] not in ('DAY', 'GTD')
            or type(raw['expires_at_ns']) is not int or not 0 < raw['expires_at_ns'] < 2**63
            or type(raw['created_at_ns']) is not int or raw['created_at_ns'] < 0
            or type(raw['updated_at_ns']) is not int or raw['updated_at_ns'] < raw['created_at_ns']
            or raw['intake_local_state'] != 'RECEIVED' or type(raw['risk_selected']) is not bool
            or any(raw[key] != 'UNKNOWN' for key in
                   ('risk_local_state', 'gateway_local_state', 'cancel_local_state'))):
        raise ValueError
    instrument = MarketInstrumentKey.from_dict(raw['instrument'])
    fields = {key: raw[key] for key in _FIELDS - {'status', 'source', 'instrument'}}
    return ExecutionManagementParentSnapshot(instrument=instrument, **fields)


class ExecutionManagementReadClient:
    """Borrow a threaded runtime and independently configured CURVE target.

    Expected peer, key and endpoint are trusted local inputs. The target's
    registration fields are placeholders and a matching current registration
    is rebound for each call. Neither an observation nor local time authorizes
    a write. The service independently authorizes and audits every query.
    """

    def __init__(self, runtime, *, target, grant_revision, timeout_seconds=5.0):
        if (type(runtime) is not ThreadedServiceClientRuntime
                or type(target) is not BoundExecutionManagementReadTarget
                or not _reference(grant_revision)
                or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError('INVALID_EXECUTION_MANAGEMENT_READ_CLIENT')
        BoundExecutionManagementReadTarget.__post_init__(target)
        self._runtime = runtime
        self._target = replace(target, scope=replace(target.scope), identity=replace(target.identity),
                               expected_peer=replace(target.expected_peer))
        self._revision, self._timeout, self._pid = grant_revision, timeout_seconds, os.getpid()

    def _registration(self):
        target = self._target
        BoundExecutionManagementReadTarget.__post_init__(target)
        snapshot = self._runtime.status()
        matches = [binding for binding in snapshot.bindings
                   if binding.active is True and binding.required is True
                   and binding.mount == target.mount and binding.service_id == target.service_id
                   and binding.instance_id == target.instance_id and binding.scope_ref == target.scope_ref
                   and binding.scope == target.scope and type(binding.operations) is frozenset
                   and PARENT_READ_V1 in binding.operations and binding.authenticated_peer == target.expected_peer
                   and binding.server_key_fingerprint == target.server_key_fingerprint]
        if (os.getpid() != self._pid or snapshot.state != 'running' or not snapshot.thread_alive
                or not snapshot.healthy or snapshot.identity != target.identity
                or snapshot.run_id != target.runtime_run_id or len(matches) != 1
                or not _reference(matches[0].registration_id) or not _reference(matches[0].service_epoch)):
            raise ValueError('EXECUTION_MANAGEMENT_ROUTE_UNAVAILABLE')
        return matches[0].registration_id, matches[0].service_epoch

    def query(self, parent_ref, *, query_id):
        """Read one parent; reusing query_id performs a fresh audited query."""
        ExecutionManagementReadRequest(self._target.instance_id, self._target.identity, self._target.scope,
                                       self._revision, PARENT_READ_V1, parent_ref, query_id)
        try:
            registration = self._registration()
            target = replace(self._target, registration_id=registration[0], service_epoch=registration[1])
            raw = _detach_wire(self._runtime.call_bound(target, PARENT_READ_V1,
                {'schema_version': 1, 'parent_ref': parent_ref, 'query_id': query_id,
                 'grant_revision': self._revision}, timeout_seconds=self._timeout))
            if (self._registration() != registration or type(raw) is not dict
                    or set(raw) != {'schema_version', 'snapshot'}
                    or type(raw['schema_version']) is not int or raw['schema_version'] != 1):
                raise ValueError
            return _observation(_parse_snapshot(raw['snapshot'], parent_ref))
        except Exception:
            return _observation()
