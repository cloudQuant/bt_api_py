"""Opt-in real ServiceHost/CURVE registration with only synthetic journals.

No order is submitted to Gateway or a provider. This observes one Registry,
not a global single-process fence or agreement with SDK routing configuration.
"""
from dataclasses import replace
import os
import queue
import socket
import sqlite3
import threading

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.contracts import ExecutionScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.gateway_execution_subject_scopes import GatewayExecutionSubjectBinding, GatewayExecutionSubjectScopes
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_transport_zmq.server import CurveClientCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceClient
from tests.integration.test_iter63_gateway_direct_service import _service_config, SERVICE, DOMAIN, ACCOUNT
from tests.unit.test_service_registry import _next_request

pytestmark = pytest.mark.skipif(
    os.environ.get('BT_API_SYNTHETIC_CURVE_LOOPBACK') != '1' or not zmq.has('curve'),
    reason='requires explicit synthetic localhost CURVE sandbox opt-in')

A = WireIdentity(WireRole.STRATEGY_CLIENT, 'curve-a', 'selected')
B = WireIdentity(WireRole.STRATEGY_CLIENT, 'curve-b', 'selected')
D = WireIdentity(WireRole.STRATEGY_CLIENT, 'curve-direct', 'direct')
U = WireIdentity(WireRole.STRATEGY_CLIENT, 'curve-unknown', 'unknown')

def scope(identity, run='run-a'):
    return WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref, run_id=run)


def free_local_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Harness:
    def __init__(self, path):
        self.path = path
        self.keys = {i.principal_ref: zmq.curve_keypair() for i in (A, B, D, U)}
        self.server_public, self.server_secret = zmq.curve_keypair()
        self.failures = queue.Queue()
        self.started = queue.Queue()
        self.stop = threading.Event()
        self.journal_id = None
        self.thread = None
        self.clients = []

    def start(self, selected):
        endpoint = f'tcp://127.0.0.1:{free_local_port()}'
        config = _service_config(endpoint)
        resource_ops = frozenset({'direct.reserve'})
        grants = {i.principal_ref: (PrincipalGrant(i, (ResourceGrant('gateway-a', 'service.direct_write',
                    'scope-a', replace(scope(i), run_id=None), resource_ops),)),) for i in (A, B, D, U)}
        authority = TrustedServiceAuthority(identity=SERVICE, scope=WireScope(DOMAIN), scope_ref='scope-a',
            scope_version='scope-v1', security_ref='security-a', security_version='security-v1',
            registration_policy_ref='direct-policy-v1', registration_policy_version='direct-policy-v1',
            service_requirements_ref='direct-requirements-v1', service_requirements_version='direct-requirements-v1',
            curve_public_key=self.server_public, curve_secret_key=self.server_secret,
            principals={keys[0]: principal for principal, keys in self.keys.items()}, grants=grants)
        selected_wire, direct_wire = replace(scope(A), run_id=None), replace(scope(D), run_id=None)
        declaration = GatewayExecutionSubjectScopes(frozenset({ACCOUNT}), {
            selected_wire: ExecutionScope('ctp', 'simnow', 'synthetic-account', 'selected'), direct_wire: None})
        self.stop.clear()
        def serve():
            host = None
            try:
                journal_path = self.path / 'gateway.sqlite'
                if self.journal_id is None:
                    journal = GatewayDirectCommandJournal.create(journal_path, ACCOUNT, generation='g1')
                    self.journal_id = journal.journal_id
                else:
                    journal = GatewayDirectCommandJournal(journal_path, ACCOUNT, generation='g1',
                        expected_journal_id=self.journal_id, minimum_revision=0)
                def resources(*_):
                    binding = GatewayDirectServiceBinding(journal, {
                        i.principal_ref: GatewayDirectGrant(i.actor_ref, frozenset({'rb'})) for i in (A, B, D, U)})
                    values = {'binding': binding}
                    if selected:
                        values['execution_subject_binding'] = GatewayExecutionSubjectBinding(declaration, journal)
                    return ComponentResources.borrowed(values, ready=lambda: True)
                host = ServiceHost(config, authority, self.path / 'registry.sqlite', resources)
                host.start()
                self.started.put(host.endpoint)
                while not self.stop.is_set():
                    host.serve_once(20)
            except BaseException as exc:
                self.failures.put(exc)
                self.started.put(None)
            finally:
                if host is not None:
                    try:
                        host.close()
                    except BaseException as exc:
                        self.failures.put(exc)
        self.thread = threading.Thread(target=serve, name='synthetic-gateway-host')
        self.thread.start()
        actual = self.started.get(timeout=8)
        if actual is None:
            raise self.failures.get_nowait()
        assert actual == endpoint and actual.startswith('tcp://127.0.0.1:')
        self.endpoint = actual

    def client(self, identity):
        public, secret = self.keys[identity.principal_ref]
        client = ZmqServiceClient(self.endpoint, SERVICE,
            credentials=CurveClientCredentials(self.server_public, public, secret))
        self.clients.append(client)
        return client

    def close(self):
        for client in self.clients:
            client.close()
        self.clients.clear()
        self.stop.set()
        if self.thread is not None:
            self.thread.join(8)
            assert not self.thread.is_alive()
            self.thread = None
        if not self.failures.empty():
            raise self.failures.get_nowait()


def register(client, identity, key, run='run-a'):
    return client.request(_next_request('service.register', {
        'registration_key': key, 'client_session_id': key, 'instance_id': 'gateway-a',
        'port': 'service.direct_write', 'scope_ref': 'scope-a', 'operations': ['direct.reserve']},
        identity=identity, scope=scope(identity, run)), timeout=3).payload


def unregister(client, identity, receipt):
    return client.request(_next_request('service.unregister', {
        'registration_id': receipt['registration_id'], 'service_epoch': receipt['service_epoch']},
        identity=identity, scope=scope(identity)), timeout=3).payload


def denied_without_run(reply):
    assert not reply['ok'] and 'strategy_write_run_id' not in str(reply)


def test_real_curve_host_pins_subject_and_keeps_explicit_direct(tmp_path):
    harness = Harness(tmp_path)
    try:
        harness.start(selected=True)
        a, b, direct, unknown = (harness.client(identity) for identity in (A, B, D, U))
        receipt = register(a, A, 'a-first')['result']
        denied_without_run(register(b, B, 'b-active'))
        # A different authenticated key cannot impersonate A's wire identity.
        denied_without_run(register(b, A, 'forged-a'))
        assert register(direct, D, 'ordinary')['ok']
        denied_without_run(register(unknown, U, 'unknown'))
        assert unregister(a, A, receipt)['ok']
        denied_without_run(register(b, B, 'b-after-release'))
        denied_without_run(register(b, B, 'b-new-run', run='new-run'))
        suggestion = register(a, A, 'a-new-run', run='new-run')
        assert not suggestion['ok'] and suggestion['result']['strategy_write_run_id'] == 'run-a'
        assert register(a, A, 'a-reconnect')['ok']
        harness.close()
        harness.start(selected=True)
        a, b = harness.client(A), harness.client(B)
        denied_without_run(register(b, B, 'b-after-restart'))
        assert register(a, A, 'a-after-restart')['ok']
    finally:
        harness.close()


def test_real_curve_unknown_preexisting_claim_is_never_adopted(tmp_path):
    harness = Harness(tmp_path)
    try:
        harness.start(selected=False)
        a = harness.client(A)
        assert register(a, A, 'legacy-a')['ok']
        harness.close()
        harness.start(selected=True)
        a, b = harness.client(A), harness.client(B)
        denied_without_run(register(a, A, 'legacy-resume'))
        denied_without_run(register(b, B, 'legacy-takeover'))
        with sqlite3.connect(tmp_path / 'registry.sqlite') as db:
            assert db.execute('SELECT count(*) FROM execution_subject_pins').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM execution_subject_unknown_history').fetchone()[0] == 1
        harness.close()
        harness.start(selected=True)
        denied_without_run(register(harness.client(B), B, 'legacy-after-restart'))
    finally:
        harness.close()
