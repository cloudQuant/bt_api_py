"""Strategy-side, source-bound RPC for the experimental local intake only.

The caller must durably retain the exact original request before submit. A
local receipt, rejection or missing row is not Gateway acceptance, a venue
fact, or proof that an earlier request cannot still arrive. No retry is made.
LOCAL_REJECTED is compatible with the experimental fresh-intake rejection
schema; a service without that schema may return only an unknown outcome.
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
from dataclasses import dataclass, replace

from bt_api_base.service_protocol import encode_wire_value

from bt_api_py.execution_parent_grant import QUERY, SUBMIT
from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.service_runtime import BoundExecutionParentTarget, ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime, _copy_wire


@dataclass(frozen=True)
class ExecutionParentObservation:
    outcome: str
    request_id: str
    request_sha256: str
    parent_ref: str | None = None
    code: str | None = None


class ExecutionParentClient:
    """Borrow a threaded runtime and independently configured target.

    Target peer, endpoint and key fingerprint must come from trusted local
    composition, not registration status or replies. The runtime must use a
    ``ZmqTransportFactory`` with ``strict_single_connection=True``. Its registration fields
    are placeholders: only the current matching authenticated registration is
    selected per call. This client is not a trading or BT order adapter.
    """

    def __init__(self, runtime, *, target, timeout_seconds=5.0):
        if (type(runtime) is not ThreadedServiceClientRuntime
                or type(target) is not BoundExecutionParentTarget
                or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
            raise ValueError('INVALID_EXECUTION_PARENT_CLIENT')
        BoundExecutionParentTarget.__post_init__(target)
        self._runtime = runtime
        self._target = replace(target, scope=replace(target.scope), identity=replace(target.identity),
                               expected_peer=replace(target.expected_peer))
        self._timeout = timeout_seconds
        self._pid = os.getpid()

    def _registration(self, operation):
        target = self._target
        BoundExecutionParentTarget.__post_init__(target)
        snapshot = self._runtime.status()
        matches = [binding for binding in snapshot.bindings
                   if binding.active is True and binding.required is True
                   and binding.mount == target.mount and binding.service_id == target.service_id
                   and binding.instance_id == target.instance_id and binding.scope_ref == target.scope_ref
                   and binding.scope == target.scope and type(binding.operations) is frozenset
                   and operation in binding.operations and binding.authenticated_peer == target.expected_peer
                   and binding.server_key_fingerprint == target.server_key_fingerprint]
        if (os.getpid() != self._pid or snapshot.state != 'running' or not snapshot.thread_alive
                or not snapshot.healthy or snapshot.identity != target.identity or snapshot.run_id != target.run_id
                or len(matches) != 1 or type(matches[0].registration_id) is not str
                or not matches[0].registration_id or type(matches[0].service_epoch) is not str
                or not matches[0].service_epoch):
            raise ValueError('EXECUTION_PARENT_ROUTE_UNAVAILABLE')
        return matches[0].registration_id, matches[0].service_epoch

    def _request(self, request):
        try:
            value = _copy_wire(request)
            if type(value) is not dict or set(value) != {'request_id', 'order_type', 'spec'}:
                raise ValueError
            spec, request_id = value['spec'], value['request_id']
            if (type(request_id) is not str or not request_id or request_id != request_id.strip()
                    or type(value['order_type']) is not str or not value['order_type']
                    or type(spec) is not dict
                    or (value['order_type'] == 'limit'
                        and (type(spec.get('parent_ref')) is not str or not spec['parent_ref']))
                    or spec.get('account_scope') != self._target.scope.account_ref
                    or spec.get('strategy_scope') != self._target.scope.strategy_ref
                    or spec.get('execution_domain_ref') != self._target.scope.execution_domain_ref):
                raise ValueError
            digest = hashlib.sha256(encode_wire_value(value)).hexdigest()
            return value, request_id, digest
        except Exception:
            raise ValueError('INVALID_EXECUTION_PARENT_REQUEST') from None

    def submit(self, request):
        """Send exactly one request; a caller must persist it before calling."""
        value, request_id, digest = self._request(request)
        return self._call(SUBMIT, value, request_id, digest, value['spec'].get('parent_ref'), value['order_type'])

    def query(self, original_request):
        """Query the original ID and compare any row with the original bytes."""
        value, request_id, digest = self._request(original_request)
        return self._call(QUERY, {'request_id': request_id}, request_id, digest, value['spec'].get('parent_ref'), value['order_type'])

    def _call(self, operation, arguments, request_id, digest, parent_ref, order_type):
        try:
            registration = self._registration(operation)
            target = replace(self._target, registration_id=registration[0], service_epoch=registration[1])
        except Exception:
            return ExecutionParentObservation('NOT_SENT', request_id, digest, code='ROUTE_UNAVAILABLE')
        try:
            raw = _detach_wire(self._runtime.call_bound(target, operation, arguments, timeout_seconds=self._timeout))
        except ServiceRuntimeRequestNotSentError:
            return ExecutionParentObservation('NOT_SENT', request_id, digest, code='REQUEST_NOT_SENT')
        except ThreadedRuntimeError as error:
            if error.code in {'NOT_READY', 'QUEUE_FULL', 'TIMEOUT_BEFORE_START', 'CLOSED_BEFORE_START',
                              'INVALID_ARGUMENTS', 'WRONG_PROCESS', 'OWNER_THREAD_REENTRY'}:
                return ExecutionParentObservation('NOT_SENT', request_id, digest, code=error.code)
            return ExecutionParentObservation('UNKNOWN', request_id, digest, code='CALL_OUTCOME_UNKNOWN')
        except Exception:
            return ExecutionParentObservation('UNKNOWN', request_id, digest, code='CALL_OUTCOME_UNKNOWN')
        try:
            if self._registration(operation) != registration or type(raw) is not dict or raw.get('local_only') is not True:
                raise ValueError
            if operation == QUERY and type(raw.get('found')) is not bool:
                raise ValueError
            if operation == QUERY and raw == {'local_only': True, 'found': False}:
                return ExecutionParentObservation('LOCAL_NOT_FOUND', request_id, digest)
            prefix = {'local_only': True}
            if operation == QUERY:
                prefix['found'] = True
            received = dict(prefix, status='RECEIVED', request_id=request_id, parent_ref=parent_ref,
                            request_sha256=digest)
            rejected = dict(prefix, status='REJECTED', code='UNSUPPORTED_ORDER_TYPE',
                            request_id=request_id, request_sha256=digest)
            if order_type == 'limit' and raw == received:
                return ExecutionParentObservation('LOCAL_RECEIVED', request_id, digest, parent_ref)
            if order_type != 'limit' and raw == rejected:
                return ExecutionParentObservation('LOCAL_REJECTED', request_id, digest, code='UNSUPPORTED_ORDER_TYPE')
        except Exception:
            pass
        return ExecutionParentObservation('UNKNOWN', request_id, digest, code='INVALID_OR_UNBOUND_REPLY')
