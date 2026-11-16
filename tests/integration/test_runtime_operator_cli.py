"""Authenticated loopback of Runtime control with synthetic Gateway resources."""
import http.client
import json
import queue
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_run_contract import (
    AccessMode,
    ApiConfig,
    GatewayConfig,
    MonitorEventsConfig,
    OperatorConfig,
    OperatorRole,
    RestartConfig,
    StopConfig,
    UnitConfig,
    UnitKind,
    ValidatedRuntimeConfig,
)

from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
    GatewayRuntimeControlProof,
)
from bt_api_py.runtime_gateway_control import RuntimeGatewayControlRoute
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def make_unit(tmp_path, *, name, access=AccessMode.DIRECT):
    directory = tmp_path / ('strategy_' + name)
    directory.mkdir(parents=True, exist_ok=True)
    entry = directory / 'run.py'
    entry.write_text('# synthetic strategy\n', encoding='utf-8')
    gateway = GatewayConfig(str(tmp_path / ('gateway_' + name))) if access is AccessMode.GATEWAY else None
    return UnitConfig(name, UnitKind.STRATEGY, str(directory), str(entry), sys.executable, (), access,
                      1.0, 10.0, StopConfig(5.0, 1.0, 1.0 if gateway else None),
                      RestartConfig(), gateway=gateway)


def make_config(tmp_path, unit):
    return ValidatedRuntimeConfig('bt_api_runtime/v1', str(tmp_path / 'runtime_home'),
        ApiConfig('http_json', '127.0.0.1', free_port(), 'subject_token'),
        (OperatorConfig('alice', OperatorRole.OPERATOR, ('*',)),), MonitorEventsConfig(False), (unit,))


def control_payload(operation, *, unit=None, parameters=None, command_id=None, reason=''):
    return {'schema': 'bt_api_run_contract.control/v1', 'operation': operation, 'unit': unit,
            'parameters': parameters or {}, 'command_id': command_id, 'reason': reason,
            'confirm': None, 'all_units': False}


def request(port, payload, *, token):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('POST', '/v1/control', body=json.dumps(payload), headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token,
            'Host': f'127.0.0.1:{port}'})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


OPS = frozenset({'direct.control.apply', 'direct.control.status', 'direct.control.resume'})


def _client(endpoint, client, route):
    return parse_client_config({'client_config_schema_version': 1, 'process_role': 'application_client',
        'registration': {'application_ref': client.actor_ref},
        'clients': {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [{
            'mount': 'application.direct_write', 'service_id': route.service_id, 'instance_id': route.instance_id,
            'scope_ref': route.scope_ref, 'account_ref': route.scope.account_ref, 'transport_ref': 'gateway'}],
            'options': {'request_timeout_ms': 1000}}}},
        'communication': {'config_schema_version': 1, 'profiles': {'gateway': {
            'backend': 'zmq', 'config_schema_version': 1, 'endpoints': {'rpc_connect': endpoint},
            'security_profile_ref': 'client-key'}}}})


def test_cli_gateway_control_requires_identity_and_shuts_down(tmp_path):
    unit = make_unit(tmp_path, name='alpha', access=AccessMode.GATEWAY)
    strategy = str(uuid.uuid4())
    server_revoked = False
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        endpoint = f'tcp://127.0.0.1:{probe.getsockname()[1]}'
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    service = WireIdentity(WireRole.SERVICE, 'gateway-service', 'service')
    client = WireIdentity(WireRole.APPLICATION_CLIENT, 'runtime-principal', 'runtime-app')
    scope = WireScope('domain', account_ref='account', strategy_ref=strategy)
    route = RuntimeGatewayControlRoute('service', 'gateway', 'scope', scope)
    config = _client(endpoint, client, route)
    expiry = time.time() + 120
    server_proof = GatewayRuntimeControlProof('delegation', 'alice', expiry, revoked=server_revoked)
    service_config = parse_service_config({'service_config_schema_version': 1, 'process_role': 'service',
        'service_id': 'service', 'scope_ref': 'scope', 'transport_ref': 'server',
        'registration_policy_ref': 'policy', 'service_requirements_ref': 'requirements',
        'components': {'gateway': {'config_schema_version': 2, 'settings': {'instance_id': 'gateway',
            'bind_ports': ['service.direct_write'], 'options': {'role': 'trading_owner',
                'dispatch_enabled': True, 'control_enabled': True}}}},
        'communication': {'config_schema_version': 1, 'profiles': {'server': {'backend': 'zmq',
            'config_schema_version': 1, 'endpoints': {'rpc_bind': endpoint}, 'security_profile_ref': 'key'}}}})
    grant = ResourceGrant('gateway', 'service.direct_write', 'scope', scope, OPS)
    authority = TrustedServiceAuthority(identity=service, scope=WireScope('domain'), scope_ref='scope',
        scope_version='v1', security_ref='key', security_version='v1', registration_policy_ref='policy',
        registration_policy_version='v1', service_requirements_ref='requirements', service_requirements_version='v1',
        curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
        principals={client_keys[0]: client.principal_ref}, grants={client.principal_ref: (PrincipalGrant(client, (grant,)),)})
    ready, stop = queue.Queue(), threading.Event()
    (tmp_path / 'guard').mkdir()

    def serve():
        owner = host = None
        try:
            state = []
            def factory():
                guard = LocalAccountSessionGuard(tmp_path / 'guard', 'account', 'owner')
                journal = GatewayDirectCommandJournal.create(tmp_path / 'journal', 'account', generation=guard.acquire())
                dispatcher = GatewayDirectDispatcher(journal, 'TEST', guard, lambda *_: None, lambda _: (), lambda _: True)
                state.append((journal, guard))
                return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
            owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=10, busy_timeout_seconds=10)
            assert owner.start(10)
            journal, guard = state[0]
            control = owner.submit_service_call(lambda: journal.enable_controls(guard)).result(timeout_seconds=5)
            grants = {client.principal_ref: GatewayControlGrant(client.actor_ref, False, frozenset({strategy}),
                OPS, kind='runtime', runtime_proof=server_proof)}
            service_binding = GatewayDirectServiceBinding(journal, {strategy: GatewayDirectGrant('s', frozenset({'X'}))},
                owner=owner, control=control, control_grants=grants)
            host = ServiceHost(service_config, authority, tmp_path / 'registry',
                lambda *_: ComponentResources.borrowed({'binding': service_binding}, ready=lambda: True))
            host.start()
            ready.put(('ready', grants))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as exc:
            ready.put(('error', repr(exc)))
        finally:
            if host is not None and host.state == 'started':
                host.close()
            if owner is not None:
                owner.close(10)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    process = None
    try:
        status, _ = ready.get(timeout=20)
        assert status == 'ready'
        marker = tmp_path / 'strategy-executed'
        Path(unit.entry).write_text(f"open({str(marker)!r}, 'w').write('executed')\n")
        runtime_path = tmp_path / 'runtime.json'
        port = free_port()
        runtime_path.write_text(json.dumps({
            'schema': 'bt_api_runtime/v1', 'home': str(tmp_path / 'runtime-home'),
            'api': {'transport': 'http_json', 'bind': '127.0.0.1', 'port': port,
                    'identity_mechanism': 'subject_token'},
            'operators': [{'name': 'alice', 'role': 'operator', 'units': ['alpha']}],
            'monitor_events': {'enabled': False}, 'units': [{
                'name': 'alpha', 'kind': 'strategy', 'dir': unit.strategy_dir,
                'entry': 'run.py', 'python': sys.executable, 'access': 'gateway',
                'heartbeat_interval_s': 1, 'start_timeout_s': 10,
                'stop': {'graceful_timeout_s': 2, 'kill_timeout_s': 1, 'drain_timeout_s': 1},
                'gateway': {'account_dir': str(tmp_path / 'gateway')},
            }]}))
        def secret(name, data):
            path = tmp_path / name
            path.write_bytes(data)
            path.chmod(0o600)
            return str(path)
        token = 'synthetic-token-' + 'a' * 32
        tokens = secret('tokens.json', json.dumps({'schema': 'bt_api_py.runtime_subject_tokens.v1',
            'tokens': [{'token': token, 'operator': 'alice'}]}).encode())
        keyfiles = {'server_public': secret('server.public', server_keys[0]),
                    'client_public': secret('runtime.public', client_keys[0]),
                    'client_secret': secret('runtime.secret', client_keys[1])}
        config_path = tmp_path / 'operator.json'
        config_path.write_text(json.dumps({'schema': 'bt_api_py.runtime_operator.v1',
            'runtime_config_file': str(runtime_path), 'subject_token_file': tokens,
            'gateway': {'identity': {'principal_ref': client.principal_ref, 'actor_ref': client.actor_ref},
                'expected_peer': {'principal_ref': service.principal_ref, 'actor_ref': service.actor_ref},
                'timeout_seconds': 5, 'routes': [{'unit': 'alpha', 'strategy_uuid': strategy,
                    'route': {'service_id': route.service_id, 'instance_id': route.instance_id,
                        'scope_ref': route.scope_ref, 'execution_domain_ref': 'domain', 'account_ref': 'account'},
                    'client_config': config.to_public_dict(), 'client_config_fingerprint': config.fingerprint(),
                    'curve_keys': keyfiles, 'delegation': {'delegation_id': 'delegation', 'operator_ref': 'alice',
                                                            'expires_at': expiry, 'revoked': False}}]},
            'monitor': None}))
        process = subprocess.Popen([sys.executable, '-u', '-m', 'bt_api_py.runtime_operator_cli', 'run',
                                    '--config', str(config_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        assert select.select([process.stdout], [], [], 20)[0]
        line = process.stdout.readline().strip()
        assert line == f'runtime_control_listening 127.0.0.1 {port}', (line, process.poll())
        payload = control_payload('runtime.units.list')
        status, result = request(port, payload, token='wrong-token')
        assert result['code'] == 'unauthenticated', result
        status, result = request(port, payload, token=token)
        assert result['status'] == 'OK', result
        status, refused = request(port, control_payload('runtime.unit.pause', unit='alpha',
             command_id=str(uuid.uuid4()), reason='synthetic test'), token=token)
        assert refused['status'] == 'REJECTED' and refused['code'] == 'gateway_control_port_unavailable', refused
        assert not marker.exists()
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=15)
        assert process.returncode == 0, (out, err)
        assert 'runtime_control_closed' in out and token not in out + err
        assert not marker.exists()
    finally:
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=15)
        stop.set()
        thread.join(15)
        assert not thread.is_alive()


def test_cli_monitor_events_with_real_monitor_host(tmp_path):

    from bt_api_py.service_config import parse_service_config
    from tests.integration import test_runtime_monitor_ingress as fixture
    monitor_path, doc, keys = fixture._deployment(tmp_path)
    monitor_process = fixture.monitor_fixture.start(monitor_path)
    process = None
    try:
        unit = make_unit(tmp_path, name='alpha')
        runtime_path = tmp_path / 'runtime.json'
        port = free_port()
        runtime_path.write_text(json.dumps({
            'schema': 'bt_api_runtime/v1', 'home': str(tmp_path / 'runtime-home'),
            'api': {'transport': 'http_json', 'bind': '127.0.0.1', 'port': port,
                    'identity_mechanism': 'subject_token'},
            'operators': [{'name': 'alice', 'role': 'operator', 'units': ['alpha']}],
            'monitor_events': {'enabled': True}, 'units': [{
                'name': 'alpha', 'kind': 'strategy', 'dir': unit.strategy_dir,
                'entry': 'run.py', 'python': sys.executable, 'access': 'direct',
                'heartbeat_interval_s': 1, 'start_timeout_s': 10,
                'stop': {'graceful_timeout_s': 2, 'kill_timeout_s': 1},
            }]}))
        token = 'synthetic-token-' + 'a' * 32
        token_path = tmp_path / 'tokens.json'
        token_path.write_text(json.dumps({'schema': 'bt_api_py.runtime_subject_tokens.v1',
                                         'tokens': [{'token': token, 'operator': 'alice'}]}))
        token_path.chmod(0o600)
        listener = doc['ingress']
        client = fixture.monitor_fixture.client(listener, keys['events'][0], keys['producer'],
                                                 'producer', 'events.append')
        public_client = parse_service_config(client._config).to_public_dict()
        config_path = tmp_path / 'operator.json'
        config_path.write_text(json.dumps({'schema': 'bt_api_py.runtime_operator.v1',
            'runtime_config_file': str(runtime_path), 'subject_token_file': str(token_path), 'gateway': None,
            'monitor': {'identity': {'principal_ref': 'producer', 'actor_ref': 'producer-service'},
                'expected_peer': {'principal_ref': listener['principal_ref'], 'actor_ref': listener['service_id']},
                'execution_domain_ref': 'synthetic-domain', 'account_ref': 'synthetic-account',
                'scope_ref': listener['scope_ref'], 'run_id': 'runtime-run', 'timeout_seconds': 2,
                'client_config': public_client,
                'curve_keys': {'server_public': str(tmp_path / 'events.public'),
                               'client_public': str(tmp_path / 'producer.public'),
                               'client_secret': str(tmp_path / 'producer.secret')}}}))
        process = subprocess.Popen([sys.executable, '-u', '-m', 'bt_api_py.runtime_operator_cli', 'run',
                                    '--config', str(config_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        assert select.select([process.stdout], [], [], 20)[0]
        line = process.stdout.readline().strip()
        assert line == f'runtime_control_listening 127.0.0.1 {port}', (line, process.poll())
        _, result = request(port, control_payload('runtime.unit.pause', unit='alpha',
            command_id=str(uuid.uuid4()), reason='test'), token=token)
        assert result['status'] == 'REJECTED'
        deadline = time.monotonic() + 5
        rows = []
        while not rows and time.monotonic() < deadline:
            rows = fixture._rows(tmp_path)
            if not rows:
                time.sleep(.02)
        assert rows
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=15)
        assert process.returncode == 0, (out, err)
        assert 'runtime_control_closed' in out and token not in out + err
    finally:
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=15)
        fixture.monitor_fixture.stop(monitor_process)


def test_cli_management_only_empty_units(tmp_path):
    port = free_port()
    runtime_path = tmp_path / 'runtime.json'
    runtime_path.write_text(json.dumps({'schema': 'bt_api_runtime/v1', 'home': str(tmp_path / 'home'),
        'api': {'transport': 'http_json', 'bind': '127.0.0.1', 'port': port,
                'identity_mechanism': 'subject_token'},
        'operators': [{'name': 'alice', 'role': 'operator', 'units': ['*']}],
        'monitor_events': {'enabled': False}, 'units': []}))
    token = 'synthetic-token-' + 'a' * 32
    token_path = tmp_path / 'tokens.json'
    token_path.write_text(json.dumps({'schema': 'bt_api_py.runtime_subject_tokens.v1',
                                     'tokens': [{'token': token, 'operator': 'alice'}]}))
    token_path.chmod(0o600)
    path = tmp_path / 'operator.json'
    path.write_text(json.dumps({'schema': 'bt_api_py.runtime_operator.v1',
        'runtime_config_file': str(runtime_path), 'subject_token_file': str(token_path),
        'gateway': None, 'monitor': None}))
    process = subprocess.Popen([sys.executable, '-u', '-m', 'bt_api_py.runtime_operator_cli', 'run',
                                '--config', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    try:
        assert select.select([process.stdout], [], [], 20)[0]
        line = process.stdout.readline().strip()
        assert line == f'runtime_control_listening 127.0.0.1 {port}', (line, process.poll())
        _, response = request(port, control_payload('runtime.units.list'), token=token)
        assert response['status'] == 'OK' and response['units'] == []
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=15)
        assert process.returncode == 0 and 'runtime_control_closed' in out, (out, err)
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=15)
