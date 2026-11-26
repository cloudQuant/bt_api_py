"""Authenticated loopback of Runtime control with synthetic Gateway resources."""
import hashlib
import http.client
import json
import queue
import select
import signal
import socket
import sqlite3
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
    UnitState,
    ValidatedRuntimeConfig,
)
from bt_api_runtime.control_ledger import ControlDatabase
from bt_api_runtime.supervision import SystemClock
from bt_api_runtime.supervision.store import RUN_SCHEMA, RunRecord

from bt_api_py import _win32_security
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


def _wait_startup_line(process, timeout=20):
    """Bounded first-line read that works on Windows too.

    select() only accepts sockets there; a reader thread + queue gives the
    same bounded wait without changing what is asserted about the CLI.
    """
    ready: queue.Queue = queue.Queue(maxsize=1)

    def _read():
        ready.put(process.stdout.readline())

    threading.Thread(target=_read, daemon=True).start()
    try:
        return ready.get(timeout=timeout).strip()
    except queue.Empty as exc:
        raise AssertionError("runtime CLI did not report startup") from exc


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


def set_owner_only(path, mode=0o600):
    if _win32_security.WINDOWS:
        _win32_security._set_owner_only_acl(path)
        owner = _win32_security.inspect_owner(path)
        assert owner.is_current_user and not owner.others_writable
    else:
        path.chmod(mode)
    return path


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


def test_observe_only_preserves_existing_run_and_in_flight_command(tmp_path):
    unit = make_unit(tmp_path, name='alpha')
    second_unit = make_unit(tmp_path, name='beta')
    runtime_home = tmp_path / 'runtime-home'
    run_path = runtime_home / 'runs' / 'alpha.json'
    run_path.parent.mkdir(parents=True)
    historical_record = RunRecord(
        schema=RUN_SCHEMA,
        unit_name='alpha',
        strategy_uuid='11111111-1111-4111-8111-111111111111',
        run_id='33333333-3333-4333-8333-333333333333',
        process=None,
        spawned=False,
        adopted=False,
        started_at_utc='2026-11-26T05:00:00+00:00',
        started_monotonic=1.0,
        os_boot_id=None,
        stdout_path=str(runtime_home / 'logs' / 'alpha.stdout.log'),
        stderr_path=str(runtime_home / 'logs' / 'alpha.stderr.log'),
        state=UnitState.STARTING,
        updated_at_utc='2026-11-26T05:00:00+00:00',
    )
    historical_run = json.dumps(
        historical_record.to_mapping(), sort_keys=True, separators=(',', ':')
    ).encode('utf-8')
    run_path.write_bytes(historical_run)

    pending_command = str(uuid.uuid4())
    claimed_command = str(uuid.uuid4())
    database = ControlDatabase(runtime_home, SystemClock())
    try:
        for command_id, historical_unit, unit_uuid in (
            (pending_command, unit, '11111111-1111-4111-8111-111111111111'),
            (claimed_command, second_unit, '22222222-2222-4222-8222-222222222222'),
        ):
            assert database.ledger.begin(
                command_id,
                operator='alice',
                client='cli',
                action='start',
                unit_name=historical_unit.name,
                unit_uuid=unit_uuid,
                unit_dir=historical_unit.strategy_dir,
                parameters={},
                reason='historical in-flight command',
                confirmation_required=False,
                confirmation_consumed=False,
            ).kind == 'new'
        assert database.ledger.claim(claimed_command)
    finally:
        database.close()

    port = free_port()
    runtime_path = tmp_path / 'runtime.json'
    runtime_path.write_text(json.dumps({
        'schema': 'bt_api_runtime/v1',
        'home': str(runtime_home),
        'api': {
            'transport': 'http_json',
            'bind': '127.0.0.1',
            'port': port,
            'identity_mechanism': 'subject_token',
        },
        'operators': [{'name': 'alice', 'role': 'operator', 'units': ['alpha']}],
        'monitor_events': {'enabled': False},
        'units': [
            {
                'name': configured.name,
                'kind': 'strategy',
                'dir': configured.strategy_dir,
                'entry': 'run.py',
                'python': sys.executable,
                'access': 'direct',
                'heartbeat_interval_s': 1,
                'start_timeout_s': 10,
                'stop': {'graceful_timeout_s': 2, 'kill_timeout_s': 1},
            }
            for configured in (unit, second_unit)
        ],
    }))
    token = 'synthetic-token-' + 'o' * 32
    token_path = tmp_path / 'tokens.json'
    token_path.write_text(json.dumps({
        'schema': 'bt_api_py.runtime_subject_tokens.v1',
        'tokens': [{'token': token, 'operator': 'alice'}],
    }))
    set_owner_only(token_path)
    config_path = tmp_path / 'operator.json'
    config_path.write_text(json.dumps({
        'schema': 'bt_api_py.runtime_operator.v1',
        'runtime_config_file': str(runtime_path),
        'subject_token_file': str(token_path),
        'gateway': None,
        'monitor': None,
    }))

    denied_command = str(uuid.uuid4())
    process = subprocess.Popen(
        [
            sys.executable,
            '-u',
            '-m',
            'bt_api_py.runtime_operator_cli',
            'run',
            '--config',
            str(config_path),
            '--observe-only',
            '--enable-owner-stop-file',
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert _wait_startup_line(process) == 'runtime_control_mode observe_only'
        assert _wait_startup_line(process) == f'runtime_control_listening 127.0.0.1 {port}'
        marker_line = _wait_startup_line(process)
        prefix = 'runtime_control_stop_file '
        assert marker_line.startswith(prefix)
        marker_name = marker_line[len(prefix):]
        assert '/' not in marker_name and '\\' not in marker_name

        _, listed = request(port, control_payload('runtime.units.list'), token=token)
        assert listed['status'] == 'OK'
        _, denied = request(
            port,
            control_payload(
                'runtime.unit.start',
                unit='alpha',
                command_id=denied_command,
                reason='must remain read only',
            ),
            token=token,
        )
        assert denied['status'] == 'REJECTED'
        assert denied['code'] == 'not_authorized'
        assert run_path.read_bytes() == historical_run

        marker = runtime_home / marker_name
        marker.write_bytes(b'')
        set_owner_only(marker)
        out, err = process.communicate(timeout=15)
        assert process.returncode == 0, (out, err)
        assert 'runtime_control_closed' in out
        assert token not in out + err
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=15)

    assert run_path.read_bytes() == historical_run
    database = ControlDatabase(runtime_home, SystemClock())
    try:
        assert database.ledger.get(pending_command).state == 'PENDING'
        assert database.ledger.get(claimed_command).state == 'CLAIMED'
        assert database.ledger.get(denied_command) is None
    finally:
        database.close()


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
            set_owner_only(path)
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
        line = _wait_startup_line(process, 20)
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
        set_owner_only(token_path)
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
        line = _wait_startup_line(process, 20)
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


def test_runtime_cli_event_is_read_by_authenticated_monitor_query_host(tmp_path):
    import re

    from bt_api_monitor.observation_query import (
        sign_observation_query_request,
        verify_observation_query_page,
    )

    from bt_api_py.service_runtime import (
        BoundMonitorQueryTarget,
        ClientCurveKeys,
        ServiceClientRuntime,
        ZmqTransportFactory,
    )
    from tests.integration import test_runtime_monitor_ingress as monitor_fixture
    from tests.unit import test_monitor_query_service_host as query_fixture

    event_type = "runtime.command_accepted.v1"
    monitor_path, monitor_doc, monitor_keys = monitor_fixture._deployment(tmp_path)
    monitor_process = monitor_fixture.monitor_fixture.start(monitor_path)
    runtime_process = None
    runtime_stop_marker = None
    query_process = None
    query_stop_marker = None
    query_lines = None
    query_client = None
    try:
        unit = make_unit(tmp_path, name="alpha")
        runtime_path = tmp_path / "runtime.json"
        runtime_home = tmp_path / "runtime-home"
        port = free_port()
        runtime_path.write_text(json.dumps({
            "schema": "bt_api_runtime/v1",
            "home": str(runtime_home),
            "api": {"transport": "http_json", "bind": "127.0.0.1", "port": port,
                    "identity_mechanism": "subject_token"},
            "operators": [{"name": "alice", "role": "operator", "units": ["alpha"]}],
            "monitor_events": {"enabled": True},
            "units": [{
                "name": "alpha", "kind": "strategy", "dir": unit.strategy_dir,
                "entry": "run.py", "python": sys.executable, "access": "direct",
                "heartbeat_interval_s": 1, "start_timeout_s": 10,
                "stop": {"graceful_timeout_s": 2, "kill_timeout_s": 1},
            }],
        }), encoding="utf-8")

        token = "synthetic-token-" + "a" * 32
        token_path = tmp_path / "tokens.json"
        token_path.write_text(json.dumps({
            "schema": "bt_api_py.runtime_subject_tokens.v1",
            "tokens": [{"token": token, "operator": "alice"}],
        }), encoding="utf-8")
        set_owner_only(token_path)

        listener = monitor_doc["ingress"]
        ingress_config = monitor_fixture.monitor_fixture.client(
            listener,
            monitor_keys["events"][0],
            monitor_keys["producer"],
            "producer",
            "events.append",
        )
        public_client = parse_service_config(ingress_config._config).to_public_dict()
        runtime_config_path = tmp_path / "operator.json"
        runtime_config_path.write_text(json.dumps({
            "schema": "bt_api_py.runtime_operator.v1",
            "runtime_config_file": str(runtime_path),
            "subject_token_file": str(token_path),
            "gateway": None,
            "monitor": {
                "identity": {"principal_ref": "producer", "actor_ref": "producer-service"},
                "expected_peer": {
                    "principal_ref": listener["principal_ref"],
                    "actor_ref": listener["service_id"],
                },
                "execution_domain_ref": monitor_doc["scope"]["execution_domain_ref"],
                "account_ref": monitor_doc["scope"]["account_ref"],
                "scope_ref": listener["scope_ref"],
                "run_id": "runtime-query-run",
                "timeout_seconds": 2,
                "client_config": public_client,
                "curve_keys": {
                    "server_public": str(tmp_path / "events.public"),
                    "client_public": str(tmp_path / "producer.public"),
                    "client_secret": str(tmp_path / "producer.secret"),
                },
            },
        }), encoding="utf-8")

        runtime_process = subprocess.Popen(
            [sys.executable, "-u", "-m", "bt_api_py.runtime_operator_cli", "run",
             "--config", str(runtime_config_path), "--enable-owner-stop-file"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        assert _wait_startup_line(runtime_process, 20) == f"runtime_control_listening 127.0.0.1 {port}"
        runtime_marker_line = _wait_startup_line(runtime_process, 10)
        runtime_marker_match = re.fullmatch(
            r"runtime_control_stop_file (runtime-[0-9a-f]{32}\.stop)", runtime_marker_line
        )
        assert runtime_marker_match is not None, runtime_marker_line
        runtime_stop_marker = runtime_home / runtime_marker_match.group(1)
        assert runtime_process.poll() is None

        command_id = str(uuid.uuid4())
        _, result = request(
            port,
            control_payload(
                "runtime.unit.pause", unit="alpha", command_id=command_id, reason="test"
            ),
            token=token,
        )
        assert result["status"] == "REJECTED"
        assert runtime_process.poll() is None
        assert monitor_process.poll() is None

        # This read selects the exact event identity for the query assertion;
        # the authenticated query response below is the read-path evidence.
        expected_event = None
        deadline = time.monotonic() + 5
        while expected_event is None and time.monotonic() < deadline:
            selected_rows = monitor_fixture._rows(tmp_path)
            if selected_rows:
                expected_event = selected_rows[0].event
            else:
                time.sleep(0.02)
        assert expected_event is not None
        runtime_event_kind = event_type[len("runtime."):-len(".v1")]
        with sqlite3.connect(
            (runtime_home / "events.sqlite3").resolve().as_uri() + "?mode=ro", uri=True
        ) as runtime_db:
            runtime_rows = runtime_db.execute(
                "SELECT seq, epoch, kind, command_id FROM events "
                "WHERE kind = ? AND command_id = ?",
                (runtime_event_kind, command_id),
            ).fetchall()
        assert len(runtime_rows) == 1
        runtime_sequence, runtime_epoch, stored_kind, stored_command_id = runtime_rows[0]
        assert type(runtime_sequence) is int and runtime_sequence > 0
        assert stored_kind == runtime_event_kind
        assert stored_command_id == command_id

        source_event_preimage = json.dumps(
            [["producer", "producer-service", None], runtime_epoch, runtime_sequence],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        source_event_id = "runtime-event:" + hashlib.sha256(source_event_preimage).hexdigest()
        runtime_payload = {
            "schema": "sdk.runtime_observation.v1",
            "sequence": runtime_sequence,
            "command_id": command_id,
        }
        monitor_event_data = {
            "producer": {
                "role": "service",
                "principal_ref": "producer",
                "actor_ref": "producer-service",
                "delegate_ref": None,
            },
            "source_event_id": source_event_id,
            "payload": runtime_payload,
        }
        assert expected_event.event_type == f"runtime.{stored_kind}.v1" == event_type
        outbox_scope = monitor_doc["storage"]["outbox_scope"]
        assert expected_event.scope == outbox_scope
        assert dict(expected_event.data) == monitor_event_data

        query_raw = query_fixture._config(tmp_path)
        query_server_public, query_server_secret = query_raw.pop("_test_keys")
        query_client_public, query_client_secret = zmq.curve_keypair()
        hmac_key = bytes(range(32))
        query_scope = WireScope(
            monitor_doc["scope"]["execution_domain_ref"],
            account_ref=monitor_doc["scope"]["account_ref"],
        )
        query_raw["scope"] = {
            "execution_domain_ref": query_scope.execution_domain_ref,
            "account_ref": query_scope.account_ref,
        }
        query_raw["storage"].update({
            "events_db": str(tmp_path / "data" / "events.sqlite"),
            "outbox_scope": outbox_scope,
            "event_types": [event_type],
        })
        query_raw["keys"]["client_public_key_file"] = str(tmp_path / "query-client.public")
        key_files = {
            "server.public": query_server_public,
            "server.secret": query_server_secret,
            "query-client.public": query_client_public,
        }
        for name, value in key_files.items():
            key_path = tmp_path / name
            key_path.write_bytes(value)
            query_fixture._set_owner_only(key_path)
        hmac_path = tmp_path / "observation.hmac.hex"
        hmac_path.write_text(hmac_key.hex(), encoding="ascii")
        query_fixture._set_owner_only(hmac_path)

        query_config_path = tmp_path / "monitor-query.json"
        query_document = query_fixture._write_config(query_config_path, query_raw)
        query_endpoint = query_document["service_config"]["communication"]["profiles"][
            "monitor-query-listener"
        ]["endpoints"]["rpc_bind"]
        query_process = subprocess.Popen(
            [sys.executable, "-u", "-m", "bt_api_py.monitor_query_service_host", "run",
             "--config", str(query_config_path), "--enable-owner-stop-file"],
            cwd=Path(__file__).resolve().parents[2],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        query_lines = queue.Queue()
        threading.Thread(
            target=query_fixture._read_lines,
            args=(query_process, query_lines),
            daemon=True,
        ).start()
        assert query_fixture._next_line(query_lines, query_process) == "MONITOR_OBSERVATION_QUERY_READY"
        query_marker_match = re.fullmatch(
            r"MONITOR_OBSERVATION_QUERY_STOP_FILE (monitor-query-[0-9a-f]{32}\.stop)",
            query_fixture._next_line(query_lines, query_process),
        )
        assert query_marker_match is not None
        query_stop_marker = query_config_path.with_name(query_marker_match.group(1))
        assert query_process.poll() is None

        service_identity = WireIdentity(
            WireRole.SERVICE,
            query_fixture._SERVICE_PRINCIPAL,
            query_fixture._SERVICE_ID,
        )
        client_identity = WireIdentity(
            WireRole.APPLICATION_CLIENT,
            query_fixture._CLIENT_PRINCIPAL,
            query_fixture._CLIENT_ACTOR,
        )
        client_config = parse_client_config(
            query_fixture._monitor_client_config(query_endpoint)
        )
        query_client = ServiceClientRuntime(
            client_config,
            identity=client_identity,
            scope_resolver=lambda _scope_ref: query_scope,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory({
                "monitor-query-client-security": ClientCurveKeys(
                    query_server_public, query_client_public, query_client_secret
                ),
            }),
            requested_operations={"application.monitor": frozenset({"observation.query"})},
        )
        receipt = query_client.start()
        assert receipt.degraded_count == 0
        binding = receipt.bindings[0]
        peer_proof = query_client.authenticated_peer_for_binding(binding)
        assert peer_proof is not None
        public_query_config = client_config.to_public_dict()
        client_profile = public_query_config["communication"]["profiles"][
            "monitor-query-client"
        ]
        target = BoundMonitorQueryTarget(
            mount=binding.mount,
            port="service.monitor_query",
            service_id=binding.service_id,
            instance_id=binding.instance_id,
            scope_ref=binding.scope_ref,
            scope=binding.scope,
            identity=client_identity,
            runtime_run_id=query_client.run_id,
            transport_ref=binding.transport_ref,
            security_profile_ref=client_profile["security_profile_ref"],
            endpoint=client_profile["endpoints"]["rpc_connect"],
            expected_peer=peer_proof[0],
            server_key_fingerprint=peer_proof[1],
            registration_id=binding.registration_id,
            service_epoch=binding.service_epoch,
        )
        signed_request = sign_observation_query_request(
            key=hmac_key,
            principal_ref=query_fixture._CLIENT_PRINCIPAL,
            scope=query_scope,
            event_types=(event_type,),
            epoch=1,
            cursor=None,
            page_size=10,
            request_id=uuid.uuid4().hex,
            issued_at=time.time(),
        )
        page = query_fixture._json_ready(query_client.call_bound(
            target,
            "observation.query",
            signed_request,
            deadline_monotonic=time.monotonic() + 3,
        ))
        assert verify_observation_query_page(
            page,
            key=hmac_key,
            principal_ref=query_fixture._CLIENT_PRINCIPAL,
            scope=query_scope,
            event_types=(event_type,),
            epoch=1,
        )
        assert page["state"] == "collected"
        assert page["event_types"] == [event_type]
        assert page["scope"]["execution_domain_ref"] == query_scope.execution_domain_ref
        assert page["scope"]["account_ref"] == query_scope.account_ref
        matching_events = [
            event for event in page["events"]
            if event["event_id"] == expected_event.event_id
        ]
        assert len(matching_events) == 1
        assert matching_events[0]["event_type"] == event_type
        assert matching_events[0]["data"] == monitor_event_data

        query_client.close()
        query_client = None

        runtime_stop_marker.write_bytes(b"")
        set_owner_only(runtime_stop_marker)
        runtime_output, runtime_error = runtime_process.communicate(timeout=15)
        assert runtime_process.returncode == 0, (runtime_output, runtime_error)
        assert "runtime_control_closed" in runtime_output.splitlines()
        assert token not in runtime_output + runtime_error
        assert runtime_stop_marker.is_file()

        query_stop_marker.write_bytes(b"")
        set_owner_only(query_stop_marker)
        query_process.wait(timeout=10)
        query_output = query_fixture._drain_output(query_lines)
        assert "MONITOR_OBSERVATION_QUERY_STOPPED" in query_output
        assert "MONITOR_OBSERVATION_QUERY_FAILED" not in query_output
        assert query_process.returncode == 0
        assert query_stop_marker.is_file()

        monitor_process.stop_marker.write_bytes(b"")
        set_owner_only(monitor_process.stop_marker)
        monitor_output, monitor_error = monitor_process.communicate(timeout=15)
        assert monitor_process.returncode == 0
        assert "MONITOR_STOPPED" in monitor_output
        assert monitor_process.stop_marker.is_file()
    finally:
        if query_client is not None:
            query_client.close()
        if runtime_process is not None and runtime_process.poll() is None:
            if runtime_stop_marker is not None:
                runtime_stop_marker.write_bytes(b"")
                query_fixture._set_owner_only(runtime_stop_marker)
            else:
                fallback = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)
                runtime_process.send_signal(fallback)
            runtime_process.wait(timeout=15)
        if query_process is not None and query_process.poll() is None:
            query_fixture._stop_query_process(query_process, query_stop_marker)
            if query_lines is not None:
                query_fixture._drain_output(query_lines)
        if monitor_process.poll() is None:
            monitor_fixture.monitor_fixture.stop(monitor_process)


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
    set_owner_only(token_path)
    path = tmp_path / 'operator.json'
    path.write_text(json.dumps({'schema': 'bt_api_py.runtime_operator.v1',
        'runtime_config_file': str(runtime_path), 'subject_token_file': str(token_path),
        'gateway': None, 'monitor': None}))
    process = subprocess.Popen([sys.executable, '-u', '-m', 'bt_api_py.runtime_operator_cli', 'run',
                                '--config', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    try:
        line = _wait_startup_line(process, 20)
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
