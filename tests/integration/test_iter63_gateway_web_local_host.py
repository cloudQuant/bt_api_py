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

import httpx
import pytest
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
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


@pytest.mark.parametrize('change', ['rewrite_restore', 'rename_restore'])
def test_local_host_protected_login_and_real_curve(tmp_path, change):
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
        config_path.chmod(0o600)
        denied_config = json.loads(json.dumps(config))
        denied_config['operators'][0]['operations'].append('direct.control.resume')
        denied_path = (tmp_path / 'denied.json').resolve()
        denied_path.write_text(json.dumps(denied_config), encoding='utf-8')
        denied_path.chmod(0o600)
        with pytest.raises(RuntimeError, match='GATEWAY_UNAVAILABLE'):
            LocalGatewayWebHost(denied_path).start()
        host = LocalGatewayWebHost(config_path).start()
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

            # Test changes that happen entirely between requests and restore the old bytes/mtime.
            original = config_path.read_bytes()
            before = config_path.stat()
            if change == 'rewrite_restore':
                config_path.write_bytes(b'changed')
                config_path.write_bytes(original)
                os.utime(config_path, ns=(before.st_atime_ns, before.st_mtime_ns))
            else:
                moved = config_path.with_name('moved.json')
                config_path.rename(moved)
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
