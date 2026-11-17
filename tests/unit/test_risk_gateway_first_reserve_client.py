"""Synthetic same-owner Risk/Gateway first-receipt bridge; no account authority."""
from dataclasses import replace
import hashlib
import json
import pickle
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest
import zmq

from bt_api_base.gateway_risk_first_reserve_v2 import GatewayRiskFirstReserveResultV2
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope, WIRE_PROTOCOL_VERSION, wire_contract_digest
from bt_api_py.risk_gateway_first_reserve_client import (
    RiskGatewayFirstReserveBridge, RiskGatewayFirstReserveError, VerifiedRiskFirstResult,
)
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from bt_api_py.service_runtime import BoundServiceTarget, ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_transport_zmq import service_v2 as transport
from tests.unit.test_iter65_live_source_capability import Context, PEER, config
from tests.unit.test_service_registry import _next_request, _register


OP = 'direct.risk_first_reserve.v2'
RISK_OP = 'risk.order.reserve.v2'


class Peer:
    def __init__(self):
        self.calls = []
        self.reply_change = None
        self.before_reply = None

    def handle(self, request):
        op = request.header.operation
        if op == 'service.discover':
            return {'protocol_version': WIRE_PROTOCOL_VERSION, 'wire_contract': wire_contract_digest(),
                    'service_id': 'synthetic-service', 'ready': True, 'capabilities': [{
                        'instance_id': 'synthetic-instance', 'port': 'service.direct_write', 'operations': [OP]}]}
        if op == 'service.register':
            return {'registration_id': 'gateway-registration', 'service_epoch': 'gateway-epoch',
                    'operations': [OP], 'expires_at': time.time() + 120, 'lease_remaining_seconds': 120,
                    'policy_version': 'policy', 'requirements_version': 'requirements'}
        if op == 'service.unregister':
            return {'unregistered': True, 'registration_id': 'gateway-registration'}
        assert op == OP
        self.calls.append(request)
        args = request.payload['arguments']
        from bt_api_base.gateway_risk_first_reserve_v2 import GatewayRiskFirstReserveRequestV2
        from bt_api_py.service_runtime_thread import _copy_wire
        original = GatewayRiskFirstReserveRequestV2.from_mapping(_copy_wire(args['request']))
        command = original.command
        reply = GatewayRiskFirstReserveResultV2(
            request_commitment_digest=original.digest, journal_id='journal', owner_generation='generation',
            reservation_generation='generation', account_scope=command.account_scope,
            strategy_scope=command.strategy_scope, execution_domain_ref=command.execution_domain_ref,
            run_id=command.run_id, request_key=command.request_key, reservation_id='reservation',
            payload_digest=command.payload_digest, remaining_ttl_ms=original.requested_ttl_ms).to_mapping()
        if self.reply_change is not None:
            self.reply_change(reply)
        if self.before_reply is not None:
            self.before_reply()
        return {'status': 'RESERVED', 'result': reply}


@pytest.fixture
def bridge_harness(tmp_path, monkeypatch, request):
    # Reuse only publicly checked-in test inputs. The v4 source guard is bypassed
    # in this test process solely to exercise the later receipt path.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'bt_api/bt_api_risk/tests'))
    from test_pretrade_attempt_v4 import new_v4, data
    from bt_api_risk.pretrade import ledger as ledger_module
    monkeypatch.setattr(ledger_module, '_source_unverified', lambda _db: False)
    real_transport = getattr(request, 'param', False) is True
    if not real_transport:
        monkeypatch.setattr(transport.zmq, 'Context', lambda **_kwargs: Context(peer))
    peer = Peer()
    with new_v4(tmp_path / 'risk.sqlite') as h:
        binding, values, request = data(h)
        payload = json.loads(values['payload_json'])
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal-a', binding.strategy_ref)
        scope = WireScope(request.command.execution_domain_ref, account_ref=binding.account_scope,
                          strategy_ref=binding.strategy_ref, run_id=binding.run_id)
        template = replace(scope, run_id=None)
        cap = ServiceCapability('risk-a', 'service.risk_order', frozenset({RISK_OP}), frozenset({RISK_OP}))
        grant = ResourceGrant('risk-a', 'service.risk_order', 'risk-scope', template, frozenset({RISK_OP}))
        registry = ServiceRegistry(':memory:', service_id='risk-service', capabilities=(cap,),
            grants={'principal-a': (PrincipalGrant(identity, (grant,)),)}, policy_version='policy',
            requirements_version='requirements', enable_risk_v2_dispatch_admission=True)
        captured = []
        registry.register_handler('risk-a', RISK_OP, lambda ctx, args: captured.append((ctx, args)) or {})
        registered = _register(registry, principal='principal-a', identity=identity, scope=scope, key='key',
            instance_id='risk-a', port='service.risk_order', scope_ref='risk-scope', operations=(RISK_OP,))
        assert registered['ok']
        rec = registered['result']
        binding = replace(binding, registration_id=rec['registration_id'], service_epoch=rec['service_epoch'])
        command = replace(request.command, actor_principal='risk-service-principal', actor_ref='risk-actor')
        request = replace(request, command=command, risk_instance_id='risk-a',
                          risk_registration_id=binding.registration_id, risk_service_epoch=binding.service_epoch)
        incoming = {'request_key': binding.request_key, 'payload': payload, 'parent_ref': None, 'child_id': None}
        reply = registry.handle('principal-a', _next_request(RISK_OP, {'registration_id': binding.registration_id,
            'service_epoch': binding.service_epoch, 'instance_id': 'risk-a', 'arguments': incoming},
            identity=identity, scope=scope))
        assert reply['ok']
        context, arguments = captured[0]
        gateway_identity = WireIdentity(WireRole.SERVICE, command.actor_principal, command.actor_ref)
        server, server_secret = zmq.curve_keypair()
        public, secret = zmq.curve_keypair()
        from bt_api_py.service_config import parse_service_config
        config_data = config().to_public_dict()
        config_data['clients']['gateway']['settings']['bindings'][0]['account_ref'] = scope.account_ref
        server_thread = None
        stop = None
        endpoint = 'tcp://127.0.0.1:1'
        if real_transport:
            import queue
            import threading
            from bt_api_transport_zmq.server import CurveServerCredentials
            from bt_api_transport_zmq.service_v2 import ZmqServiceServer
            ready, stop = queue.Queue(), threading.Event()
            def run_server():
                server_obj = ZmqServiceServer('tcp://127.0.0.1:*', PEER,
                    {public: gateway_identity.principal_ref},
                    lambda _principal, message: {'ok': True, 'code': 'OK', 'result': peer.handle(message)},
                    credentials=CurveServerCredentials(server, server_secret))
                ready.put(server_obj.endpoint)
                try:
                    while not stop.is_set():
                        server_obj.serve_once(timeout_ms=20)
                finally:
                    server_obj.close()
            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()
            endpoint = ready.get(timeout=5)
        config_data['communication']['profiles']['gateway']['endpoints']['rpc_connect'] = endpoint
        runtime = ServiceClientRuntime(parse_service_config(config_data), identity=gateway_identity, scope_resolver=lambda _: scope,
            peer_resolver=lambda _: PEER, transport_factory=ZmqTransportFactory(
                {'synthetic-key': ClientCurveKeys(server, public, secret)}, strict_single_connection=True),
            run_id=scope.run_id, requested_operations={'upstream.direct_write': frozenset({OP})})
        runtime.start()
        gateway_binding, = runtime.bindings
        target = BoundServiceTarget('upstream.direct_write', 'service.direct_write', 'synthetic-service',
            'synthetic-instance', 'synthetic-account', scope, gateway_identity, scope.run_id, 'gateway',
            'synthetic-key', endpoint, PEER, hashlib.sha256(server).hexdigest(),
            gateway_binding.registration_id, gateway_binding.service_epoch)
        bridge = RiskGatewayFirstReserveBridge(ledger=h.ledger, runtime=runtime, context=context, arguments=arguments,
                                               binding=binding, target=target, request=request, payload=payload)
        try:
            yield h, bridge, values, request, peer, runtime, target, registry, context, arguments, binding
        finally:
            context.dispatch_admission.close()
            runtime.close()
            registry.close()
            if stop is not None:
                stop.set()
                server_thread.join(5)
                assert not server_thread.is_alive()


def test_strict_receipt_once_without_dto_authority(bridge_harness):
    _, bridge, values, request, peer, _, _, _, _, _, _ = bridge_harness
    attempt = bridge.prepare(values)
    ticket = bridge.send_once(attempt, timeout_seconds=2)
    verified = bridge.consume_once(ticket)
    assert type(verified) is VerifiedRiskFirstResult
    with pytest.raises(TypeError):
        pickle.dumps(verified)
    result = bridge.take_verified(verified)
    assert result.matches_request(request) and 1 <= result.remaining_ttl_ms < request.requested_ttl_ms
    assert len(peer.calls) == 1
    for action in (lambda: bridge.send_once(attempt, timeout_seconds=1), lambda: bridge.consume_once(ticket),
                   lambda: bridge.take_verified(verified), lambda: bridge.take_verified(VerifiedRiskFirstResult())):
        with pytest.raises(RiskGatewayFirstReserveError):
            action()
    assert len(peer.calls) == 1


def test_network_call_holds_no_risk_registry_or_ledger_transaction(bridge_harness):
    h, bridge, values, _, peer, _, _, registry, _, _, _ = bridge_harness
    def observe():
        assert not registry._lock._is_owned()
        assert not registry._db.in_transaction
        with sqlite3.connect(h.path, timeout=0.1) as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()
    peer.before_reply = observe
    attempt = bridge.prepare(values)
    bridge.send_once(attempt, timeout_seconds=2)
    assert len(peer.calls) == 1


@pytest.mark.parametrize('bridge_harness', [True], indirect=True)
def test_real_local_curve_strict_socket_receipt(bridge_harness):
    _, bridge, values, request, peer, _, _, _, _, _, _ = bridge_harness
    attempt = bridge.prepare(values)
    ticket = bridge.send_once(attempt, timeout_seconds=2)
    result = bridge.take_verified(bridge.consume_once(ticket))
    assert result.matches_request(request) and len(peer.calls) == 1


def test_source_guard_still_blocks_durable_attempt(bridge_harness, monkeypatch):
    from bt_api_risk.pretrade import ledger as module
    _, bridge, values, _, peer, _, _, _, _, _, _ = bridge_harness
    monkeypatch.setattr(module, '_source_unverified', lambda _db: True)
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_ATTEMPT_UNAVAILABLE'):
        bridge.prepare(values)
    assert peer.calls == []
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.prepare(values)


def test_duplicate_attempt_and_forged_handles_never_send(bridge_harness):
    h, bridge, values, request, peer, runtime, target, registry, context, args, binding = bridge_harness
    attempt = bridge.prepare(values)
    other = RiskGatewayFirstReserveBridge(ledger=h.ledger, runtime=runtime, context=context, arguments=args,
        binding=binding, target=target, request=request, payload=json.loads(values['payload_json']))
    with pytest.raises(RiskGatewayFirstReserveError):
        other.prepare(values)
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.send_once(object(), timeout_seconds=1)
    with pytest.raises(Exception):
        runtime._consume_risk_first(bridge, attempt, object(), object())
    assert peer.calls == []


def test_internal_receipt_cannot_be_consumed_outside_admission_scope(bridge_harness):
    _, bridge, values, _, peer, runtime, _, _, _, _, _ = bridge_harness
    attempt = bridge.prepare(values)
    ticket = bridge.send_once(attempt, timeout_seconds=2)
    with pytest.raises(Exception):
        runtime._consume_risk_first(bridge, attempt, ticket, object())
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_RECEIPT_UNKNOWN'):
        bridge.consume_once(ticket)
    assert len(peer.calls) == 1


@pytest.mark.parametrize('change', ['extra', 'request_digest', 'payload_digest', 'ttl', 'state'])
def test_malformed_or_mismatched_gateway_reply_is_unknown(bridge_harness, change):
    _, bridge, values, _, peer, _, _, _, _, _, _ = bridge_harness
    def tamper(reply):
        if change == 'extra':
            reply['extra'] = 'synthetic'
        elif change == 'request_digest':
            reply['request_commitment_digest'] = 'f' * 64
        elif change == 'payload_digest':
            reply['payload_digest'] = 'f' * 64
        elif change == 'ttl':
            reply['remaining_ttl_ms'] = True
        else:
            reply['state'] = 'SENT'
    peer.reply_change = tamper
    attempt = bridge.prepare(values)
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_ATTEMPT_UNKNOWN'):
        bridge.send_once(attempt, timeout_seconds=2)
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.send_once(attempt, timeout_seconds=2)
    assert len(peer.calls) == 1


@pytest.mark.parametrize('change', ['renewed_binding', 'registration', 'socket'])
def test_changed_gateway_binding_before_consume_is_unknown(bridge_harness, change):
    _, bridge, values, _, peer, runtime, target, _, _, _, _ = bridge_harness
    attempt = bridge.prepare(values)
    ticket = bridge.send_once(attempt, timeout_seconds=2)
    old = runtime._bindings[0]
    socket = runtime._transports[(target.service_id, target.transport_ref)]._transport._socket
    try:
        if change == 'renewed_binding':
            runtime._bindings[0] = replace(old)
        elif change == 'registration':
            runtime._bindings[0] = replace(old, registration_id='new-registration')
        else:
            socket.options[zmq.RECONNECT_IVL] = 1
        with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_RECEIPT_UNKNOWN'):
            bridge.consume_once(ticket)
    finally:
        runtime._bindings[0] = old
        socket.options[zmq.RECONNECT_IVL] = -1
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.consume_once(ticket)
    assert len(peer.calls) == 1


def test_ordinary_bound_call_is_not_a_risk_first_entry(bridge_harness):
    from bt_api_py.service_runtime import ServiceRuntimeRequestNotSentError
    _, _, _, _, peer, runtime, target, _, _, _, _ = bridge_harness
    with pytest.raises(ServiceRuntimeRequestNotSentError):
        runtime.call_bound(target, OP, {}, deadline_monotonic=time.monotonic() + 1)
    assert peer.calls == []


def test_non_strict_factory_cannot_send(bridge_harness):
    _, bridge, values, _, peer, runtime, _, _, _, _, _ = bridge_harness
    old_factory = runtime._factory
    runtime._factory = ZmqTransportFactory(old_factory._profiles, strict_single_connection=False)
    try:
        attempt = bridge.prepare(values)
        with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_ATTEMPT_UNKNOWN'):
            bridge.send_once(attempt, timeout_seconds=1)
        assert peer.calls == []
    finally:
        runtime._factory = old_factory


def test_attempt_cannot_move_to_another_owner_thread(bridge_harness):
    _, bridge, values, _, peer, _, _, _, _, _, _ = bridge_harness
    attempt = bridge.prepare(values)
    errors = []
    def call():
        try:
            bridge.send_once(attempt, timeout_seconds=1)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=call)
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], RiskGatewayFirstReserveError)
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.send_once(attempt, timeout_seconds=1)
    assert peer.calls == []


def test_substituted_request_method_cannot_forge_receipt(bridge_harness, monkeypatch):
    _, bridge, values, _, peer, runtime, target, _, _, _, _ = bridge_harness
    attempt = bridge.prepare(values)
    client = runtime._transports[(target.service_id, target.transport_ref)]._transport
    monkeypatch.setattr(client, 'request', lambda *a, **kw: pytest.fail('forged method called'))
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_ATTEMPT_UNKNOWN'):
        bridge.send_once(attempt, timeout_seconds=1)
    monkeypatch.delattr(client, 'request')
    assert peer.calls == []


def test_expired_conservative_response_ttl_is_unknown(bridge_harness):
    _, bridge, values, _, peer, _, _, _, _, _, _ = bridge_harness
    attempt = bridge.prepare(values)
    ticket = bridge.send_once(attempt, timeout_seconds=2)
    time.sleep(1.05)
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_RECEIPT_UNKNOWN'):
        bridge.consume_once(ticket)
    assert len(peer.calls) == 1


def test_timeout_after_send_is_unknown_and_never_retried(bridge_harness):
    _, bridge, values, _, peer, _, _, _, _, _, _ = bridge_harness
    peer.before_reply = lambda: time.sleep(0.05)
    attempt = bridge.prepare(values)
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_ATTEMPT_UNKNOWN'):
        bridge.send_once(attempt, timeout_seconds=0.01)
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.send_once(attempt, timeout_seconds=1)
    assert len(peer.calls) == 1


@pytest.mark.parametrize('expired', ['risk_registration', 'risk_permit'])
def test_risk_expiry_during_projection_is_rejected_before_rpc(bridge_harness, monkeypatch, expired):
    from bt_api_py import risk_first_payload_consistency as projection
    h, bridge, values, _, peer, _, _, registry, context, _, _ = bridge_harness
    attempt = bridge.prepare(values)
    original = projection.validate_risk_first_payload_consistency
    def expire_after_projection(*args, **kwargs):
        result = original(*args, **kwargs)
        if expired == 'risk_registration':
            registry._db.execute('UPDATE sessions SET expires_at=? WHERE registration_id=?',
                                 (0, context.registration_id))
        else:
            h.clock.value = values['expires_ns'] + 1
        return result
    monkeypatch.setattr(projection, 'validate_risk_first_payload_consistency', expire_after_projection)
    error = None
    try:
        bridge.send_once(attempt, timeout_seconds=2)
    except RiskGatewayFirstReserveError as exc:
        error = exc
    assert peer.calls == []
    assert error is not None and str(error) == 'FIRST_ATTEMPT_UNKNOWN'
    with pytest.raises(RiskGatewayFirstReserveError):
        bridge.send_once(attempt, timeout_seconds=2)


@pytest.mark.parametrize('change', ['gateway_renewal', 'request', 'payload'])
def test_inflight_changes_make_response_unknown(bridge_harness, change):
    _, bridge, values, request, peer, runtime, _, _, _, _, _ = bridge_harness
    old_binding = runtime._bindings[0]
    def mutate():
        if change == 'gateway_renewal':
            runtime._bindings[0] = replace(old_binding)
        elif change == 'request':
            object.__setattr__(request, 'risk_permit_id', 'altered')
        else:
            bridge._payload['quantity'] = '999'
    peer.before_reply = mutate
    attempt = bridge.prepare(values)
    try:
        with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_ATTEMPT_UNKNOWN'):
            bridge.send_once(attempt, timeout_seconds=2)
        with pytest.raises(RiskGatewayFirstReserveError):
            bridge.send_once(attempt, timeout_seconds=2)
        assert len(peer.calls) == 1
    finally:
        runtime._bindings[0] = old_binding


def test_closed_risk_admission_before_consume_is_unknown(bridge_harness):
    _, bridge, values, _, peer, _, _, _, context, _, _ = bridge_harness
    attempt = bridge.prepare(values)
    ticket = bridge.send_once(attempt, timeout_seconds=2)
    context.dispatch_admission.close()
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_RECEIPT_UNKNOWN'):
        bridge.consume_once(ticket)
    assert len(peer.calls) == 1


@pytest.mark.parametrize('change', ['close', 'renew', 'registration', 'lease', 'socket', 'risk_admission'])
def test_changed_owner_after_consume_invalidates_verified_result(bridge_harness, change):
    _, bridge, values, _, peer, runtime, target, _, context, _, _ = bridge_harness
    attempt = bridge.prepare(values)
    verified = bridge.consume_once(bridge.send_once(attempt, timeout_seconds=2))
    old = runtime._bindings[0]
    old_lease = old.lease_deadline
    socket = runtime._transports[(target.service_id, target.transport_ref)]._transport._socket
    try:
        if change == 'close':
            runtime.close()
        elif change == 'renew':
            runtime._bindings[0] = replace(old, lease_deadline=old_lease + 10)
        elif change == 'registration':
            runtime._bindings[0] = replace(old, registration_id='new-registration')
        elif change == 'lease':
            object.__setattr__(old, 'lease_deadline', time.monotonic() - 1)
        elif change == 'socket':
            socket.options[zmq.RECONNECT_IVL] = 1
        else:
            context.dispatch_admission.close()
        with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_RECEIPT_UNKNOWN'):
            bridge.take_verified(verified)
    finally:
        if change != 'close':
            runtime._bindings[0] = old
            object.__setattr__(old, 'lease_deadline', old_lease)
            socket.options[zmq.RECONNECT_IVL] = -1
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_RECEIPT_UNAVAILABLE'):
        bridge.take_verified(verified)
    assert len(peer.calls) == 1


def test_authenticated_input_payload_must_match_gateway_payload(bridge_harness):
    h, _, values, request, peer, runtime, target, _, context, args, binding = bridge_harness
    payload = dict(json.loads(values['payload_json']), quantity='2')
    with pytest.raises(RiskGatewayFirstReserveError, match='FIRST_ATTEMPT_UNAVAILABLE'):
        RiskGatewayFirstReserveBridge(ledger=h.ledger, runtime=runtime, context=context, arguments=args,
                                     binding=binding, target=target, request=request, payload=payload)
    assert peer.calls == []
