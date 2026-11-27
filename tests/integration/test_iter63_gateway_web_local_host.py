"""Local web host against a synthetic, authenticated CURVE Gateway."""
import asyncio
import base64
import hashlib
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py import _win32_security as win32_security
import bt_api_py.gateway_web_local_host as host_module
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.gateway_web_local_host import LocalGatewayWebHost
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

OPS = frozenset({'direct.control.identity', 'direct.control.apply', 'direct.control.status', 'direct.control.list'})


def _run(app, callback):
    async def run():
        transport = httpx.ASGITransport(app=app, client=('127.0.0.1', 1234))
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1:8000') as browser:
            await callback(browser)
    asyncio.run(run())


def _set_owner_only(path):
    if win32_security.WINDOWS:
        win32_security._set_owner_only_acl(path)
    else:
        path.chmod(0o600)
    return path


@pytest.mark.parametrize('change', ['rewrite_restore', 'rename_restore'])
def test_local_host_protected_login_and_real_curve(tmp_path, change, monkeypatch):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        endpoint = f'tcp://127.0.0.1:{probe.getsockname()[1]}'
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    service = WireIdentity(WireRole.SERVICE, 'gateway-service', 'service')
    client = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'app')
    scope = WireScope('domain', account_ref='account')
    service_config = parse_service_config({
        'service_config_schema_version': 1, 'process_role': 'service', 'service_id': 'service',
        'scope_ref': 'scope', 'transport_ref': 'server', 'registration_policy_ref': 'policy',
        'service_requirements_ref': 'requirements',
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
        principals={client_keys[0]: 'operator'}, grants={'operator': (PrincipalGrant(client, (grant,)),)})
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
            binding = GatewayDirectServiceBinding(journal, {'strategy': GatewayDirectGrant('s', frozenset({'X'}))},
                owner=owner, control=control,
                control_grants={'operator': GatewayControlGrant('app', True, frozenset(), OPS)})
            host = ServiceHost(service_config, authority, tmp_path / 'registry',
                lambda *_: ComponentResources.borrowed({'binding': binding}, ready=lambda: True))
            host.start()
            ready.put('ready')
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as exc:
            ready.put(('error', type(exc).__name__))
        finally:
            if host is not None and host.state == 'started':
                host.close()
            if owner is not None:
                owner.close(10)

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    host = None
    try:
        assert ready.get(timeout=20) == 'ready'
        token = 'a' * 64
        config = {'schema_version': 1, 'origin': 'http://127.0.0.1:8000',
            'receipt_key_base64': base64.b64encode(os.urandom(32)).decode(), 'receipt_key_id': 'v1',
            'operators': [{'principal_ref': 'operator', 'actor_ref': 'app',
                'login_token_sha256': hashlib.sha256(token.encode()).hexdigest(), 'scope_id': 'desk',
                'scope_ref': 'scope', 'execution_domain_ref': 'domain', 'account_ref': 'account',
                'strategy_ref': None, 'service_id': 'service', 'service_principal_ref': 'gateway-service',
                'instance_id': 'gateway', 'endpoint': endpoint, 'server_public_key': server_keys[0].decode(),
                'client_public_key': client_keys[0].decode(), 'client_secret_key': client_keys[1].decode(),
                'operations': sorted(OPS)}]}
        config_path = (tmp_path / 'host.json').resolve()
        config_path.write_text(json.dumps(config), encoding='utf-8')
        _set_owner_only(config_path)
        denied_config = json.loads(json.dumps(config))
        denied_config['operators'][0]['operations'].append('direct.control.resume')
        denied_path = (tmp_path / 'denied.json').resolve()
        denied_path.write_text(json.dumps(denied_config), encoding='utf-8')
        _set_owner_only(denied_path)
        with pytest.raises(RuntimeError, match='GATEWAY_UNAVAILABLE'):
            LocalGatewayWebHost(denied_path).start()
        host = LocalGatewayWebHost(config_path).start()
        clock = [time.monotonic()]
        monkeypatch.setattr(
            host_module,
            'time',
            SimpleNamespace(monotonic=lambda: clock[0]),
        )

        async def check(browser):
            assert (await browser.get('/api/v1/session')).status_code == 401
            assert (await browser.post('/login', data={'token': token})).status_code == 403
            headers = {'Origin': 'http://127.0.0.1:8000'}
            assert (await browser.post('/login', data={'token': 'b' * 64}, headers=headers)).status_code == 401
            login = await browser.post('/login', data={'token': token}, headers=headers)
            assert login.status_code == 200 and 'set-cookie' not in login.headers
            session_token = login.json()['session']
            assert len(session_token) == 64
            assert (await browser.get('/api/v1/session')).status_code == 401
            headers['X-Gateway-Session'] = session_token
            session = (await browser.get('/api/v1/session', headers=headers)).json()
            assert session['receipt_key_persistent'] is True
            headers.update({'X-CSRF-Token': session['csrf_token'],
                            'X-Gateway-Route': session['scopes'][0]['route_fingerprint']})
            root = '/api/v1/scopes/desk/requests/'
            scope_view = (await browser.get('/api/v1/scopes/desk/causes', headers=headers)).json()
            assert scope_view.get('status') == 'OBSERVED', scope_view
            result = (await browser.post(root + 'prepare', headers=headers,
                json={'request_id': 'freeze-1', 'mode': 'FREEZE', 'cause_id': '', 'expected_version': 0,
                      'view_receipt': scope_view['scope_receipt']})).json()
            assert result.get('status') == 'PREPARED', result
            applied = (await browser.post(root + 'run', headers=headers, json={'receipt': result['receipt']})).json()
            assert applied['status'] == 'OBSERVED' and applied['active'] is True, applied
            # Independent session can be explicitly revoked without affecting the first.
            second = (await browser.post('/login', data={'token': token}, headers={'Origin': 'http://127.0.0.1:8000'})).json()['session']
            second_headers = {'Origin': 'http://127.0.0.1:8000', 'X-Gateway-Session': second}
            assert (await browser.post('/logout', headers=second_headers)).json()['status'] == 'SIGNED_OUT'
            assert (await browser.get('/api/v1/session', headers=second_headers)).status_code == 401
            assert (await browser.get('/api/v1/session', headers=headers)).status_code == 200

            # The production host uses a 1800-second monotonic session TTL. Move
            # only this module's clock so the real owner/app boundary can check
            # the exact expiry edge without waiting or changing stdlib time.
            clock[0] += 1799
            assert (await browser.get('/api/v1/session', headers=headers)).status_code == 200
            clock[0] += 1
            assert (await browser.get('/api/v1/session', headers=headers)).status_code == 401
            renewed = await browser.post(
                '/login', data={'token': token}, headers={'Origin': 'http://127.0.0.1:8000'}
            )
            assert renewed.status_code == 200
            renewed_token = renewed.json()['session']
            assert len(renewed_token) == 64 and renewed_token != session_token
            renewed_headers = {
                'Origin': 'http://127.0.0.1:8000',
                'X-Gateway-Session': renewed_token,
            }
            renewed_session = await browser.get('/api/v1/session', headers=renewed_headers)
            assert renewed_session.status_code == 200
            assert (await browser.get('/api/v1/session', headers=headers)).status_code == 401
            headers.update({
                'X-Gateway-Session': renewed_token,
                'X-CSRF-Token': renewed_session.json()['csrf_token'],
                'X-Gateway-Route': renewed_session.json()['scopes'][0]['route_fingerprint'],
            })

            # Test changes that happen entirely between requests while preserving
            # the old bytes. Windows reports st_ctime as creation time, so use a
            # new file identity there instead of relying on a transient rewrite.
            original = config_path.read_bytes()
            before = config_path.stat()
            if change == 'rewrite_restore':
                config_path.write_bytes(b'changed')
                if win32_security.WINDOWS:
                    replacement = config_path.with_name('restored.json')
                    replacement.write_bytes(original)
                    _set_owner_only(replacement)
                    os.replace(replacement, config_path)
                else:
                    config_path.write_bytes(original)
                    os.utime(config_path, ns=(before.st_atime_ns, before.st_mtime_ns))
            else:
                moved = config_path.with_name('moved.json')
                config_path.rename(moved)
                if win32_security.WINDOWS:
                    config_path.write_bytes(original)
                    _set_owner_only(config_path)
                    moved.unlink()
                else:
                    moved.rename(config_path)
            assert config_path.read_bytes() == original
            assert (await browser.get('/api/v1/session', headers=headers)).status_code == 401
            assert (await browser.post('/login', data={'token': token}, headers={'Origin': 'http://127.0.0.1:8000'})).status_code == 401
        _run(host.app, check)
    finally:
        if host is not None:
            host.close()
        stop.set()
        server.join(15)
        assert not server.is_alive()


def test_rejects_nonprivate_and_symlink_config(tmp_path):
    config = (tmp_path / 'config').resolve()
    config.write_text('{}', encoding='utf-8')
    config.chmod(0o644)
    with pytest.raises(ValueError, match='PROTECTED_FILE_UNAVAILABLE'):
        LocalGatewayWebHost(config)
    config.chmod(0o600)
    link = tmp_path / 'link'
    link.symlink_to(config)
    with pytest.raises(ValueError, match='PROTECTED_FILE_UNAVAILABLE'):
        LocalGatewayWebHost(link)


def test_fifo_config_is_rejected_without_blocking(tmp_path):
    fifo = (tmp_path / 'fifo').resolve()
    os.mkfifo(fifo, 0o600)
    result = subprocess.run([sys.executable, '-c',
        'from bt_api_py.gateway_web_local_host import _protected_bytes\n'
        'import sys\n'
        'try: _protected_bytes(sys.argv[1])\n'
        'except ValueError: sys.exit(0)\n'
        'sys.exit(1)', str(fifo)], timeout=3, capture_output=True)
    assert result.returncode == 0


def test_local_host_recovers_after_gateway_restart_with_outage(tmp_path):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        endpoint = f'tcp://127.0.0.1:{probe.getsockname()[1]}'
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    service = WireIdentity(WireRole.SERVICE, 'gateway-service', 'service')
    client = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'app')
    scope = WireScope('domain', account_ref='account')
    service_config = parse_service_config({
        'service_config_schema_version': 1, 'process_role': 'service', 'service_id': 'service',
        'scope_ref': 'scope', 'transport_ref': 'server', 'registration_policy_ref': 'policy',
        'service_requirements_ref': 'requirements',
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
        principals={client_keys[0]: 'operator'}, grants={'operator': (PrincipalGrant(client, (grant,)),)})
    (tmp_path / 'guard').mkdir()

    def start_gateway(gen_name):
        ready, stop = queue.Queue(), threading.Event()
        def serve():
            owner = gw_host = None
            try:
                state = []
                def factory():
                    guard = LocalAccountSessionGuard(tmp_path / 'guard', 'account', f'owner-{gen_name}')
                    journal = GatewayDirectCommandJournal.create(
                        tmp_path / f'journal-{gen_name}', 'account', generation=guard.acquire()
                    )
                    dispatcher = GatewayDirectDispatcher(
                        journal, 'TEST', guard, lambda *_: None, lambda _: (), lambda _: True
                    )
                    state.append((journal, guard))
                    return GatewayDirectOwnerResources(dispatcher, journal, guard.release)
                owner = GatewayDirectOwner(factory, queue_size=4, max_queue_wait_seconds=10, busy_timeout_seconds=10)
                assert owner.start(10)
                journal, guard = state[0]
                control = owner.submit_service_call(lambda: journal.enable_controls(guard)).result(timeout_seconds=5)
                binding = GatewayDirectServiceBinding(
                    journal, {'strategy': GatewayDirectGrant('s', frozenset({'X'}))},
                    owner=owner, control=control,
                    control_grants={'operator': GatewayControlGrant('app', True, frozenset(), OPS)},
                )
                gw_host = ServiceHost(
                    service_config, authority, tmp_path / f'registry-{gen_name}',
                    lambda *_: ComponentResources.borrowed({'binding': binding}, ready=lambda: True),
                )
                gw_host.start()
                ready.put('ready')
                while not stop.is_set():
                    gw_host.serve_once(10)
            except BaseException as exc:
                ready.put(('error', type(exc).__name__))
            finally:
                if gw_host is not None and gw_host.state == 'started':
                    gw_host.close()
                if owner is not None:
                    owner.close(10)
        server = threading.Thread(target=serve, daemon=True)
        server.start()
        assert ready.get(timeout=20) == 'ready'
        return stop, server

    stop1, server1 = start_gateway('g1')
    stop2 = server2 = host = None
    try:
        token = 'c' * 64
        config = {'schema_version': 1, 'origin': 'http://127.0.0.1:8000',
            'receipt_key_base64': base64.b64encode(os.urandom(32)).decode(), 'receipt_key_id': 'v1',
            'operators': [{'principal_ref': 'operator', 'actor_ref': 'app',
                'login_token_sha256': hashlib.sha256(token.encode()).hexdigest(), 'scope_id': 'desk',
                'scope_ref': 'scope', 'execution_domain_ref': 'domain', 'account_ref': 'account',
                'strategy_ref': None, 'service_id': 'service', 'service_principal_ref': 'gateway-service',
                'instance_id': 'gateway', 'endpoint': endpoint, 'server_public_key': server_keys[0].decode(),
                'client_public_key': client_keys[0].decode(), 'client_secret_key': client_keys[1].decode(),
                'operations': sorted(OPS)}]}
        config_path = (tmp_path / 'host-restart.json').resolve()
        config_path.write_text(json.dumps(config), encoding='utf-8')
        _set_owner_only(config_path)
        host = LocalGatewayWebHost(config_path).start()
        op = host._operators[0]
        first_runtime = op.runtime
        first_epoch = first_runtime.status().bindings[0].service_epoch

        async def login_and_read(browser):
            headers = {'Origin': 'http://127.0.0.1:8000'}
            login = await browser.post('/login', data={'token': token}, headers=headers)
            assert login.status_code == 200
            headers['X-Gateway-Session'] = login.json()['session']
            res = await browser.get('/api/v1/scopes/desk/causes', headers=headers)
            assert res.status_code == 200 and res.json().get('status') == 'OBSERVED'
            return headers

        headers_box = []
        _run(host.app, lambda b: asyncio.sleep(0))
        async def step1(browser):
            headers_box.append(await login_and_read(browser))
        _run(host.app, step1)
        headers = headers_box[0]

        # Stop Gateway #1 completely (true TCP outage).
        stop1.set()
        server1.join(15)
        assert not server1.is_alive()

        # Trigger request during outage: must fail closed and keep parked runtime clean.
        async def during_outage(browser):
            res = await browser.get('/api/v1/scopes/desk/causes', headers=headers)
            assert res.status_code in (200, 403)
            if res.status_code == 200:
                assert res.json().get('status') != 'OBSERVED'
        _run(host.app, during_outage)

        # Wait until runtime observes outage or hits parked MAINTENANCE_FAILED.
        # Even if renewal hasn't fired yet, force a brief outage then bring up Gateway #2.
        stop2, server2 = start_gateway('g2')

        # First call against Gateway #2 may record remote_inactive_registrations on old runtime
        # and then immediately reconnect on the next call (or reconnect immediately if already parked).
        recovered = False
        for _ in range(6):
            box = []
            async def probe_after_restart(browser):
                r = await browser.get('/api/v1/scopes/desk/causes', headers=headers)
                box.append((r.status_code, r.json()))
            _run(host.app, probe_after_restart)
            status_code, payload = box[0]
            if status_code == 200 and payload.get('status') == 'OBSERVED':
                recovered = True
                break
            time.sleep(0.2)
        assert recovered, f'LocalGatewayWebHost did not recover after Gateway restart: {box}'
        assert op.runtime is not first_runtime
        new_epoch = op.runtime.status().bindings[0].service_epoch
        assert new_epoch != first_epoch
    finally:
        if host is not None:
            host.close()
        if stop2 is not None:
            stop2.set()
            server2.join(15)
            assert not server2.is_alive()
        stop1.set()
        server1.join(15)
