"""Synthetic transport tests; these do not establish CURVE or account evidence."""
import hashlib
import queue
import threading
import time
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireEnvelope, WireIdentity, WireMessageKind, WireRole, encode_wire_value
from bt_api_transport_zmq import service_v2

from bt_api_py.execution_parent_client import ExecutionParentClient
from bt_api_py.execution_parent_grant import QUERY, SUBMIT
from bt_api_py.service_runtime import (BoundExecutionParentTarget, BoundServiceTarget,
    ServiceRuntimeRequestNotSentError, _RequestNotSentAdapter)
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.unit.test_iter65_bound_runtime import make_runtime


def setup_synthetic(monkeypatch, handler):
    """Use the real runtime and wrapper with an explicitly simulated socket."""
    made = queue.Queue()
    def factory():
        runtime, base, adapter, binding = make_runtime()
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'synthetic-strategy')
        runtime.identity = identity
        runtime._bindings = [replace(binding, mount='broker.execution_parent', operations=frozenset({SUBMIT, QUERY}))]
        old_socket = adapter._transport._socket_binding
        strict_socket = service_v2._ServiceSocketBinding(old_socket.socket, old_socket.receiver, old_socket.reply_box,
            old_socket.endpoint, old_socket.peer, old_socket.server_key, old_socket.public_key, old_socket.secret_key, True)
        adapter._transport._socket_binding = strict_socket
        adapter._transport._strict_single_connection = True
        key = (base.service_id, base.transport_ref, base.registration_id, base.service_epoch)
        proof = (adapter, adapter._transport, strict_socket)
        runtime._authenticated_registration_transports[key] = proof
        runtime._strict_registration_transports = {key: proof}
        target = BoundExecutionParentTarget('broker.execution_parent', 'service.execution_parent', base.service_id,
            base.instance_id, base.scope_ref, base.scope, identity, base.run_id, base.transport_ref,
            base.security_profile_ref, base.endpoint, base.expected_peer, base.server_key_fingerprint,
            base.registration_id, base.service_epoch)
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
        reply = WireEnvelope(replace(request.header, kind=WireMessageKind.REPLY,
            identity=transport._identity, message_id='reply', correlation_id=request.header.message_id),
            {'ok': True, 'code': 'OK', 'result': result})
        receipt = service_v2.ServiceReplyReceipt(request.header.message_id, 'reply', request.header.message_id,
            request.header.operation, time.time_ns(), time.monotonic_ns(), transport._owner_pid, threading.get_ident())
        object.__setattr__(receipt, '_socket_binding', socket_binding)
        transport._last_reply_receipt = receipt
        return reply
    monkeypatch.setattr(_RequestNotSentAdapter, 'request_bound', request_bound)
    wrapper = ThreadedServiceClientRuntime(factory)
    assert wrapper.start().healthy
    runtime, target, adapter = made.get_nowait()
    return wrapper, runtime, target, adapter


def request():
    return {'request_id': 'original-id', 'order_type': 'limit', 'spec': {
        'account_scope': 'synthetic-account', 'strategy_scope': 'synthetic-strategy',
        'execution_domain_ref': 'synthetic-domain', 'parent_ref': 'original-parent',
        'quantity': '1.00', 'additional': ['exact', 'data']}}


def test_local_receipt_and_query_preserve_request_identity_and_bytes(monkeypatch):
    submitted = request()
    expected_digest = hashlib.sha256(encode_wire_value(submitted)).hexdigest()
    calls = []
    def handler(envelope):
        calls.append(envelope)
        result = {'local_only': True, 'status': 'RECEIVED', 'request_id': 'original-id',
                  'parent_ref': 'original-parent', 'request_sha256': expected_digest}
        if envelope.header.operation == QUERY:
            result['found'] = True
        return result
    wrapper, _, target, _ = setup_synthetic(monkeypatch, handler)
    try:
        client = ExecutionParentClient(wrapper, target=target)
        assert client.submit(submitted).outcome == 'LOCAL_RECEIVED'
        assert client.query(submitted).outcome == 'LOCAL_RECEIVED'
        assert encode_wire_value(calls[0].payload['arguments']) == encode_wire_value(submitted)
        assert dict(calls[1].payload['arguments']) == {'request_id': 'original-id'}
        changed = request()
        changed['spec']['quantity'] = '1.0'
        assert client.query(changed).outcome == 'UNKNOWN'
        assert len(calls) == 3
    finally:
        wrapper.close()


def test_original_algorithm_plan_is_part_of_submit_and_query_identity(monkeypatch):
    submitted = request()
    submitted['algorithm_plan'] = {'schema_version': 1, 'plan_version': 'v1', 'build_id': 'build-1',
        'schedule': {'class': 'PASS', 'version': 1, 'binding': {'deadline_ns': 100,
            'quantity_step': '1', 'parent_limit': '5', 'price_step': '1', 'retry_limit': 0}, 'params': {}}}
    expected_digest = hashlib.sha256(encode_wire_value(submitted)).hexdigest()
    calls = []
    def handler(envelope):
        calls.append(envelope)
        result = {'local_only': True, 'status': 'RECEIVED', 'request_id': 'original-id',
                  'parent_ref': 'original-parent', 'request_sha256': expected_digest}
        if envelope.header.operation == QUERY:
            result['found'] = True
        return result
    wrapper, _, target, _ = setup_synthetic(monkeypatch, handler)
    try:
        client = ExecutionParentClient(wrapper, target=target)
        assert client.submit(submitted).outcome == 'LOCAL_RECEIVED'
        assert encode_wire_value(calls[0].payload['arguments']) == encode_wire_value(submitted)
        assert client.query(submitted).outcome == 'LOCAL_RECEIVED'
        changed = dict(submitted, algorithm_plan={**submitted['algorithm_plan'], 'build_id': 'different'})
        assert client.query(changed).outcome == 'UNKNOWN'
    finally:
        wrapper.close()


def test_bound_type_is_strategy_parent_only():
    runtime, base, _, _ = make_runtime()
    with pytest.raises(ValueError):
        replace(base, identity=WireIdentity(WireRole.STRATEGY_CLIENT, 'p', 's'))
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'p', 's')
    target = BoundExecutionParentTarget('broker.execution_parent', 'service.execution_parent', base.service_id,
        base.instance_id, base.scope_ref, base.scope, identity, base.run_id, base.transport_ref,
        base.security_profile_ref, base.endpoint, base.expected_peer, base.server_key_fingerprint,
        base.registration_id, base.service_epoch)
    with pytest.raises(ValueError):
        replace(target, mount='broker.direct_write', port='service.direct_write')
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(target, 'direct.reserve', {}, deadline_monotonic=time.monotonic() + 1)
    assert type(base) is BoundServiceTarget


def test_unavailable_route_and_lost_proof_never_send(monkeypatch):
    calls = []
    wrapper, runtime, target, adapter = setup_synthetic(monkeypatch, lambda request: calls.append(request) or {})
    try:
        client = ExecutionParentClient(wrapper, target=replace(target, service_id='other-service'))
        assert client.submit(request()).outcome == 'NOT_SENT'
        runtime._strict_registration_transports.clear()
        assert ExecutionParentClient(wrapper, target=target).submit(request()).outcome == 'NOT_SENT'
        assert calls == []
    finally:
        wrapper.close()


def test_started_timeout_is_unknown_and_no_automatic_resend(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []
    def handler(request):
        calls.append(request)
        started.set()
        release.wait(2)
        return {}
    wrapper, _, target, _ = setup_synthetic(monkeypatch, handler)
    try:
        result = ExecutionParentClient(wrapper, target=target, timeout_seconds=0.05).submit(request())
        assert started.is_set() and result.outcome == 'UNKNOWN' and len(calls) == 1
    finally:
        release.set()
        wrapper.close()


def test_local_not_found_is_only_an_observation(monkeypatch):
    wrapper, _, target, _ = setup_synthetic(monkeypatch, lambda _: {'local_only': True, 'found': False})
    try:
        result = ExecutionParentClient(wrapper, target=target).query(request())
        assert result.outcome == 'LOCAL_NOT_FOUND' and result.request_id == 'original-id'
    finally:
        wrapper.close()


def test_non_boolean_found_cannot_become_a_local_observation(monkeypatch):
    wrapper, _, target, _ = setup_synthetic(monkeypatch, lambda _: {'local_only': True, 'found': 0})
    try:
        assert ExecutionParentClient(wrapper, target=target).query(request()).outcome == 'UNKNOWN'
    finally:
        wrapper.close()


def test_non_limit_rejection_without_parent_preserves_exact_original(monkeypatch):
    original = request()
    original['order_type'] = 'market'
    del original['spec']['parent_ref']
    original['spec']['instrument'] = {'provider': 'ctp', 'venue': 'SHFE', 'environment': 'simnow',
                                      'product_type': 'future', 'instrument_id': 'rb', 'native_symbol': 'rb'}
    digest = hashlib.sha256(encode_wire_value(original)).hexdigest()
    calls = []
    def handler(envelope):
        calls.append(envelope)
        result = {'local_only': True, 'status': 'REJECTED', 'code': 'UNSUPPORTED_ORDER_TYPE',
                  'request_id': original['request_id'], 'request_sha256': digest}
        if envelope.header.operation == QUERY:
            result['found'] = True
        return result
    wrapper, _, target, _ = setup_synthetic(monkeypatch, handler)
    try:
        client = ExecutionParentClient(wrapper, target=target)
        assert client.submit(original).outcome == 'LOCAL_REJECTED'
        assert client.query(original).outcome == 'LOCAL_REJECTED'
        assert encode_wire_value(calls[0].payload['arguments']) == encode_wire_value(original)
        assert dict(calls[1].payload['arguments']) == {'request_id': original['request_id']}
        changed = {**original, 'spec': dict(original['spec'])}
        changed['spec']['quantity'] = '1.0'
        assert client.query(changed).outcome == 'UNKNOWN'
        assert len(calls) == 3
    finally:
        wrapper.close()


def test_limit_request_without_parent_is_rejected_before_send(monkeypatch):
    calls = []
    wrapper, _, target, _ = setup_synthetic(monkeypatch, lambda envelope: calls.append(envelope) or {})
    original = request()
    del original['spec']['parent_ref']
    try:
        client = ExecutionParentClient(wrapper, target=target)
        with pytest.raises(ValueError, match='INVALID_EXECUTION_PARENT_REQUEST'):
            client.submit(original)
        with pytest.raises(ValueError, match='INVALID_EXECUTION_PARENT_REQUEST'):
            client.query(original)
        assert calls == []
    finally:
        wrapper.close()


def test_queued_timeout_is_not_sent_and_never_later_delivered(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []
    def handler(envelope):
        calls.append(envelope)
        started.set()
        release.wait(2)
        return {}
    wrapper, _, target, _ = setup_synthetic(monkeypatch, handler)
    first = threading.Thread(target=lambda: wrapper.call_bound(target, SUBMIT, {}, timeout_seconds=2))
    try:
        first.start()
        assert started.wait(1)
        result = ExecutionParentClient(wrapper, target=target, timeout_seconds=0.05).submit(request())
        assert result.outcome == 'NOT_SENT' and result.code == 'TIMEOUT_BEFORE_START'
    finally:
        release.set()
        first.join(2)
        wrapper.close()
    assert len(calls) == 1 and not first.is_alive()


def test_reply_after_socket_replacement_is_unknown(monkeypatch):
    holder = {}
    calls = []
    def handler(envelope):
        calls.append(envelope)
        client = holder['adapter']._transport
        object.__setattr__(client._socket_binding, 'socket', object())
        return {'local_only': True, 'status': 'RECEIVED', 'request_id': 'original-id',
                'parent_ref': 'original-parent', 'request_sha256': hashlib.sha256(encode_wire_value(request())).hexdigest()}
    wrapper, _, target, adapter = setup_synthetic(monkeypatch, handler)
    holder['adapter'] = adapter
    try:
        assert ExecutionParentClient(wrapper, target=target).submit(request()).outcome == 'UNKNOWN'
        assert len(calls) == 1
    finally:
        wrapper.close()


def test_reply_after_registration_replacement_is_unknown(monkeypatch):
    holder = {}
    def handler(_):
        runtime = holder['runtime']
        runtime._bindings = [replace(runtime._bindings[0], service_epoch='replacement-epoch')]
        return {'local_only': True, 'status': 'RECEIVED', 'request_id': 'original-id',
                'parent_ref': 'original-parent', 'request_sha256': hashlib.sha256(encode_wire_value(request())).hexdigest()}
    wrapper, runtime, target, _ = setup_synthetic(monkeypatch, handler)
    holder['runtime'] = runtime
    try:
        assert ExecutionParentClient(wrapper, target=target).submit(request()).outcome == 'UNKNOWN'
    finally:
        wrapper.close()


def test_close_cancels_queued_parent_without_sending(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls, results = [], queue.Queue()
    def handler(envelope):
        calls.append(envelope)
        started.set()
        release.wait(2)
        return {}
    wrapper, _, target, _ = setup_synthetic(monkeypatch, handler)
    first = threading.Thread(target=lambda: wrapper.call_bound(target, SUBMIT, {}, timeout_seconds=2))
    second = threading.Thread(target=lambda: results.put(ExecutionParentClient(wrapper, target=target).submit(request())))
    closer = threading.Thread(target=lambda: wrapper.close(timeout_seconds=2))
    try:
        first.start()
        assert started.wait(1)
        second.start()
        with wrapper._condition:
            deadline = time.monotonic() + 1
            while not wrapper._queue:
                remaining = deadline - time.monotonic()
                assert remaining > 0
                wrapper._condition.wait(remaining)
        closer.start()
        second.join(1)
        result = results.get_nowait()
        assert result.outcome == 'NOT_SENT' and result.code == 'CLOSED_BEFORE_START'
    finally:
        release.set()
        first.join(2)
        closer.join(2)
    assert len(calls) == 1 and not closer.is_alive()


@pytest.mark.parametrize('reply_kind', ['valid', 'wrong_digest', 'wrong_id', 'wrong_parent', 'query_not_found', 'not_sent'])
def test_router_uses_exact_authenticated_client_and_frozen_wire(monkeypatch, tmp_path, reply_kind):
    import sqlite3
    from decimal import Decimal
    from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
    from bt_api_py._contracts.models import OrderRequest, OrderType, Side
    from bt_api_py.execution_routing import ExecutionOrderRouter, ExecutionRoutingOutcome, install_execution_routing_tables
    from bt_api_py.gateway_direct_client import GatewayClientRoute, build_gateway_order_intent
    from tests.unit.test_iter65_sdk_execution_routing import make_config

    calls = []
    connection = sqlite3.connect(tmp_path / 'routing.sqlite', isolation_level=None)
    connection.execute('BEGIN IMMEDIATE')
    install_execution_routing_tables(connection)
    connection.commit()
    def handler(envelope):
        args = dict(envelope.payload['arguments'])
        calls.append((envelope.header.operation, args))
        reader = sqlite3.connect(tmp_path / 'routing.sqlite')
        try:
            row = reader.execute('SELECT request_id, request_sha256, result FROM execution_parent_requests_v1').fetchone()
        finally:
            reader.close()
        assert row is not None
        if envelope.header.operation == SUBMIT:
            assert row[2] is None  # durable PREPARED before authenticated request
            parent = args['spec']['parent_ref']
        else:
            parent = saved['parent']
        if envelope.header.operation == QUERY and reply_kind == 'query_not_found':
            return {'local_only': True, 'found': False}
        digest = row[1] if reply_kind != 'wrong_digest' else '0' * 64
        request_id = row[0] if reply_kind != 'wrong_id' else 'foreign'
        parent = parent if reply_kind != 'wrong_parent' else 'foreign-parent'
        raw = {'local_only': True, 'status': 'RECEIVED', 'request_id': request_id,
               'parent_ref': parent, 'request_sha256': digest}
        if envelope.header.operation == QUERY:
            raw['found'] = True
        else:
            saved['parent'] = args['spec']['parent_ref']
        return raw
    saved = {}
    wrapper, _, target, _ = setup_synthetic(monkeypatch, handler)
    try:
        client_target = replace(target, service_id='unavailable') if reply_kind == 'not_sent' else target
        client = ExecutionParentClient(wrapper, target=client_target)
        # Instance-level method replacement cannot manufacture a trusted receipt.
        client.submit = lambda _: pytest.fail('instance submit override used')
        client._call = lambda *args: pytest.fail('instance _call override used')
        binding = GatewayCommandBinding('journal', target.scope.account_ref, target.scope.strategy_ref,
                                        target.identity.principal_ref)
        route = GatewayClientRoute('broker.direct_write', 'gw', 'gw-instance', 'scope', target.scope, target.identity)
        request = OrderRequest(symbol='rb', side=Side.BUY, order_type=OrderType.LIMIT,
            quantity=Decimal('5'), account_id=target.scope.account_ref, client_order_id='client',
            idempotency_key='request-1', price=Decimal('10'), quantity_unit='lots', exchange_id='SHFE', offset='open')
        intent = build_gateway_order_intent(binding, route, request)
        config = make_config(account_ref=target.scope.account_ref, strategy_ref=target.scope.strategy_ref,
                             execution_domain_ref=target.scope.execution_domain_ref)
        router = ExecutionOrderRouter(db=connection, config=config, execution_port=client,
                                      risk_selected=False, clock=lambda: 1)
        result = router.route(intent)
        expected = (ExecutionRoutingOutcome.EXECUTION_LOCAL_RECEIPT if reply_kind in {'valid', 'query_not_found'}
                    else ExecutionRoutingOutcome.EXECUTION_UNKNOWN)
        assert result.outcome is expected
        if reply_kind == 'not_sent':
            assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
            assert router.route(intent) == result and calls == []
            assert router.status(intent).outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
            assert calls == []
            return
        assert router.route(intent) == result and len(calls) == 1
        assert set(calls[0][1]) == {'request_id', 'order_type', 'spec', 'algorithm_plan'}
        assert hashlib.sha256(encode_wire_value(calls[0][1])).hexdigest() == result.request_sha256
        status = router.status(intent)
        assert status.outcome is (ExecutionRoutingOutcome.EXECUTION_UNKNOWN
                                  if reply_kind == 'query_not_found' else expected)
        assert len(calls) == 2 and calls[1][0] == QUERY
        assert calls[1][1] == {'request_id': result.request_id}
        assert len([item for item in calls if item[0] == SUBMIT]) == 1
    finally:
        wrapper.close()
        connection.close()
