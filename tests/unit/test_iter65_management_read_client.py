"""Synthetic binding tests; the transport and receipt are intentionally faked."""
import queue
import os
import threading
import time
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireEnvelope, WireIdentity, WireMessageKind, WireRole
from bt_api_transport_zmq import service_v2

from bt_api_py.execution_management_read_client import ExecutionManagementReadClient
from bt_api_py.execution_management_read_grant import PARENT_READ_V1
from bt_api_py.service_runtime import (BoundExecutionManagementReadTarget, ServiceRuntimeRequestNotSentError,
                                       _RequestNotSentAdapter)
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.unit.test_iter65_bound_runtime import make_runtime


def setup_synthetic(monkeypatch, handler):
    made = queue.Queue()

    def factory():
        runtime, base, adapter, binding = make_runtime()
        runtime._owner_pid = os.getpid()
        runtime._live_parent_capabilities_lock = threading.RLock()
        runtime._live_parent_capabilities = {}
        identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'management-principal', 'synthetic-actor')
        scope = replace(base.scope, run_id=None)
        runtime.identity = identity
        runtime._bindings = [replace(binding, mount='application.execution_management', scope=scope,
                                     operations=frozenset({PARENT_READ_V1}))]
        old = adapter._transport._socket_binding
        sock = service_v2._ServiceSocketBinding(old.socket, old.receiver, old.reply_box, old.endpoint,
            old.peer, old.server_key, old.public_key, old.secret_key, True)
        adapter._transport._socket_binding = sock
        adapter._transport._strict_single_connection = True
        key = (base.service_id, base.transport_ref, base.registration_id, base.service_epoch)
        proof = (adapter, adapter._transport, sock)
        runtime._authenticated_registration_transports[key] = proof
        runtime._strict_registration_transports = {key: proof}
        target = BoundExecutionManagementReadTarget('application.execution_management',
            'service.execution_management_read', base.service_id, base.instance_id, base.scope_ref, scope,
            identity, base.run_id, base.transport_ref, base.security_profile_ref, base.endpoint,
            base.expected_peer, base.server_key_fingerprint, base.registration_id, base.service_epoch)
        runtime.start = runtime.maintain = runtime.close = lambda: None
        runtime.next_maintenance_delay = lambda: 1.0
        made.put((runtime, target, adapter))
        return runtime

    monkeypatch.setattr(service_v2, '_strict_client_binding', lambda client: client._socket_binding)
    monkeypatch.setattr(service_v2.ZmqServiceClient, '_socket_binding_current',
                        lambda self, binding: binding is self._socket_binding and not binding.socket.closed)
    monkeypatch.setattr(service_v2, '_consume_strict_reply_receipt', lambda *args: True)

    def request_bound(adapter, request, *, timeout, deadline_monotonic, transport, socket_binding, _trusted_request):
        assert _trusted_request is True and transport is adapter._transport
        result = handler(request)
        reply = WireEnvelope(replace(request.header, kind=WireMessageKind.REPLY, identity=transport._identity,
            message_id='reply', correlation_id=request.header.message_id),
            {'ok': True, 'code': 'OK', 'result': result})
        receipt = service_v2.ServiceReplyReceipt(request.header.message_id, 'reply', request.header.message_id,
            request.header.operation, time.time_ns(), time.monotonic_ns(), transport._owner_pid, threading.get_ident())
        object.__setattr__(receipt, '_socket_binding', socket_binding)
        transport._last_reply_receipt = receipt
        return reply

    monkeypatch.setattr(_RequestNotSentAdapter, 'request_bound', request_bound)
    wrapper = ThreadedServiceClientRuntime(factory)
    assert wrapper.start().healthy
    return (wrapper, *made.get_nowait())


def response(status='OBSERVED'):
    fields = dict(journal_incarnation=None, parent_ref=None, instrument=None, side=None, offset=None,
        quantity=None, quantity_unit=None, limit_price=None, time_in_force=None, expires_at_ns=None,
        created_at_ns=None, updated_at_ns=None, intake_local_state=None, risk_selected=None,
        risk_local_state=None, gateway_local_state=None, cancel_local_state=None)
    if status == 'OBSERVED':
        fields.update(journal_incarnation='a' * 32, parent_ref='parent', instrument={
            'provider': 'ctp', 'venue': 'SHFE', 'environment': 'simnow', 'product_type': 'future',
            'instrument_id': 'rb', 'native_symbol': 'rb'}, side='buy', offset='open', quantity='2',
            quantity_unit='lots', limit_price='3', time_in_force='DAY', expires_at_ns=5,
            created_at_ns=1, updated_at_ns=2, intake_local_state='RECEIVED', risk_selected=False,
            risk_local_state='UNKNOWN', gateway_local_state='UNKNOWN', cancel_local_state='UNKNOWN')
    return {'schema_version': 1, 'snapshot': dict(status=status, source='LOCAL_JOURNAL', **fields)}


def test_refresh_same_query_and_unknown_are_local_observations(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return response('OBSERVED' if len(calls) == 1 else 'UNKNOWN')
    wrapper, _, target, _ = setup_synthetic(monkeypatch, handler)
    try:
        client = ExecutionManagementReadClient(wrapper, target=target, grant_revision='policy-v1')
        first = client.query('parent', query_id='same')
        second = client.query('parent', query_id='same')
        assert first.status == 'OBSERVED' and first.source == 'LOCAL_JOURNAL'
        assert first.snapshot.parent_ref == 'parent' and first.local_result_time_ns > 0
        assert second.status == 'UNKNOWN' and second.snapshot is None
        assert len(calls) == 2 and all(dict(call.payload['arguments']) == {
            'schema_version': 1, 'parent_ref': 'parent', 'query_id': 'same',
            'grant_revision': 'policy-v1'} for call in calls)
        assert all(call.header.scope.run_id is None for call in calls)
    finally:
        wrapper.close()


@pytest.mark.parametrize('change', ['schema', 'source', 'unknown_leak', 'parent', 'risk', 'instrument', 'timestamp'])
def test_malformed_reply_is_unknown(monkeypatch, change):
    raw = response('UNKNOWN' if change == 'unknown_leak' else 'OBSERVED')
    if change == 'schema': raw['schema_version'] = True
    if change == 'source': raw['snapshot']['source'] = 'VENUE'
    if change == 'unknown_leak': raw['snapshot']['parent_ref'] = 'parent'
    if change == 'parent': raw['snapshot']['parent_ref'] = 'another-parent'
    if change == 'risk': raw['snapshot']['risk_local_state'] = 'ACCEPTED'
    if change == 'instrument': raw['snapshot']['instrument']['venue'] = ''
    if change == 'timestamp': raw['snapshot']['updated_at_ns'] = True
    wrapper, _, target, _ = setup_synthetic(monkeypatch, lambda _: raw)
    try:
        result = ExecutionManagementReadClient(wrapper, target=target, grant_revision='policy-v1').query(
            'parent', query_id='q1')
        assert result.status == 'UNKNOWN' and result.snapshot is None
    finally:
        wrapper.close()


@pytest.mark.parametrize('field', ['quantity', 'limit_price'])
@pytest.mark.parametrize('bad', [1, True, '1e999999', '1' * 43, '1.' + '1' * 43, '-1', '0', 'NaN'])
def test_invalid_parent_amounts_are_unknown(monkeypatch, field, bad):
    raw = response()
    raw['snapshot'][field] = bad
    wrapper, _, target, _ = setup_synthetic(monkeypatch, lambda _: raw)
    try:
        result = ExecutionManagementReadClient(wrapper, target=target, grant_revision='policy-v1').query(
            'parent', query_id='q')
        assert result.status == 'UNKNOWN' and result.snapshot is None
    finally:
        wrapper.close()


def test_binding_loss_invalid_inputs_and_timeout(monkeypatch):
    started, release = threading.Event(), threading.Event()
    def handler(_):
        started.set()
        release.wait(2)
        return response()
    wrapper, runtime, target, _ = setup_synthetic(monkeypatch, handler)
    try:
        client = ExecutionManagementReadClient(wrapper, target=target, grant_revision='policy-v1', timeout_seconds=.05)
        with pytest.raises(ValueError):
            client.query('parent', query_id='')
        assert client.query('parent', query_id='q1').status == 'UNKNOWN'
        assert started.is_set()
    finally:
        release.set()
        wrapper.close()


@pytest.mark.parametrize('changed', ['key', 'run', 'scope', 'socket', 'registration'])
def test_wrong_binding_never_discloses(monkeypatch, changed):
    calls = []
    wrapper, runtime, target, adapter = setup_synthetic(monkeypatch, lambda request: calls.append(request) or response())
    try:
        if changed == 'key': target = replace(target, server_key_fingerprint='0' * 64)
        if changed == 'run': target = replace(target, runtime_run_id='other-run')
        if changed == 'scope': target = replace(target, scope=replace(target.scope, account_ref='other'))
        if changed == 'socket': runtime._strict_registration_transports.clear()
        if changed == 'registration': runtime._bindings = [replace(runtime._bindings[0], service_epoch='other')]
        result = ExecutionManagementReadClient(wrapper, target=target, grant_revision='policy-v1').query('parent', query_id='q')
        assert result.status == 'UNKNOWN' and calls == []
    finally:
        wrapper.close()


def test_target_rejects_strategy_run_and_unbound_calls(monkeypatch):
    wrapper, runtime, target, _ = setup_synthetic(monkeypatch, lambda _: response())
    try:
        with pytest.raises(ValueError):
            replace(target, scope=replace(target.scope, run_id='strategy-run'))
        with pytest.raises(ValueError):
            replace(target, identity=WireIdentity(WireRole.STRATEGY_CLIENT, 'p', 'a'))
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            wrapper.call('application.execution_management', PARENT_READ_V1, {})
        with pytest.raises(Exception):
            wrapper.call_bound(target, 'direct.reserve', {})
    finally:
        wrapper.close()


@pytest.mark.parametrize('changed', ['registration', 'socket', 'lease'])
def test_proof_lost_before_reply_never_discloses(monkeypatch, changed):
    holder = {}
    def handler(_):
        runtime, adapter = holder['runtime'], holder['adapter']
        if changed == 'registration':
            runtime._bindings = [replace(runtime._bindings[0], service_epoch='replaced')]
        elif changed == 'socket':
            adapter._transport._socket_binding.socket.closed = True
        else:
            runtime._bindings = [replace(runtime._bindings[0], lease_deadline=time.monotonic() - 1)]
        return response()
    wrapper, runtime, target, adapter = setup_synthetic(monkeypatch, handler)
    holder.update(runtime=runtime, adapter=adapter)
    try:
        result = ExecutionManagementReadClient(wrapper, target=target, grant_revision='policy-v1').query(
            'parent', query_id='q')
        assert result.status == 'UNKNOWN' and result.snapshot is None
    finally:
        wrapper.close()
