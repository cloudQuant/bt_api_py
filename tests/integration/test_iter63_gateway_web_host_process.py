"""The local Web host as a separate process next to a running synthetic Gateway.

The Gateway runs in this test process with a synthetic journal and CURVE keys;
no venue or account is contacted. The Web host is started with the module entry
point in an isolated interpreter that cannot import the AFI backend, killed, and
started again, while an ordinary operator client keeps calling the Gateway.
"""
import base64
import hashlib
import inspect
import json
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import zmq
from test_iter63_gateway_web_gateway_authority import (
    ACCOUNT,
    DOMAIN,
    OPS,
    SERVICE,
    Gateway,
    cli_call,
    operator_origin,
)

from bt_api_py.gateway_direct_service import GatewayDirectServiceBinding
from bt_api_py.gateway_web_local_host import LocalGatewayWebHost

DOC = (Path(__file__).resolve().parents[2] / 'bt_api' / 'bt_api_gateway' / 'docs'
       / 'gateway-web-api-v1.md')
# Starts the host module like ``python -m`` does, with the AFI backend's top-level
# packages made unimportable: the host must not need them.
WITHOUT_AFI = '''
import importlib.abc, runpy, sys
class NoAfi(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('app', 'ai_for_investor', 'ai_for_investor_backend'):
            raise ImportError('AFI is not installed for this test')
        return None
sys.meta_path.insert(0, NoAfi())
sys.argv = ['gateway_web_local_host', '--config', sys.argv[1]]
runpy.run_module('bt_api_py.gateway_web_local_host', run_name='__main__')
'''


def _free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def _host_config(gateway, name, port, token, operations=OPS):
    principal, actor, account, strategy = gateway.operators[name][:4]
    return {'schema_version': 1, 'origin': f'http://127.0.0.1:{port}',
            'receipt_key_base64': base64.b64encode(b'r' * 32).decode(), 'receipt_key_id': 'process-v1',
            'operators': [{'principal_ref': principal, 'actor_ref': actor,
                           'login_token_sha256': hashlib.sha256(token.encode()).hexdigest(), 'scope_id': 'desk',
                           'scope_ref': 'scope-a', 'execution_domain_ref': DOMAIN, 'account_ref': account,
                           'strategy_ref': strategy, 'service_id': SERVICE.actor_ref,
                           'service_principal_ref': SERVICE.principal_ref, 'instance_id': 'gateway-a',
                           'endpoint': gateway.endpoint, 'server_public_key': gateway.server_keys[0].decode(),
                           'client_public_key': gateway.keys[name][0].decode(),
                           'client_secret_key': gateway.keys[name][1].decode(),
                           'operations': sorted(operations)}]}


def _write_protected(path, value):
    path = path.resolve()
    path.write_text(json.dumps(value), encoding='utf-8')
    path.chmod(0o600)
    return path


class OrdinaryClient(threading.Thread):
    """An operator's own SDK client calling the Gateway the whole time."""

    def __init__(self, gateway, runtime):
        super().__init__(daemon=True)
        self.gateway, self.runtime = gateway, runtime
        self.stop, self.results = threading.Event(), []

    def run(self):
        while not self.stop.is_set():
            try:
                result = cli_call(self.runtime, 'cli', 'direct.control.identity', {}, self.gateway)
                self.results.append(('ok', result.get('status'), result.get('journal_id')))
            except Exception as exc:  # recorded and asserted on by the test
                self.results.append(('error', type(exc).__name__, None))
            time.sleep(0.02)


def _start_host(config_path, log_path):
    with open(log_path, 'ab') as log:
        return subprocess.Popen([sys.executable, '-I', '-c', WITHOUT_AFI, str(config_path)], stdout=log,
                                stderr=subprocess.STDOUT, cwd=str(config_path.parent))


def _registrations(gateway):
    return gateway.on_host(lambda host: host._registry.active_registration_ids(instance_id='gateway-a'))


def _wait_ready(process, origin, deadline=30):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        assert process.poll() is None, 'web host exited during start'
        try:
            if httpx.get(origin + '/login', timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError('web host did not start')


class Browser:
    def __init__(self, origin, token):
        self.origin, self.client = origin, httpx.Client(base_url=origin, timeout=15)
        login = self.client.post('/login', data={'token': token}, headers={'Origin': origin})
        assert login.status_code == 200, login.text
        self.headers = {'X-Gateway-Session': login.json()['session']}
        session = self.client.get('/api/v1/session', headers=self.headers).json()
        self.write = {**self.headers, 'Origin': origin, 'X-CSRF-Token': session['csrf_token'],
                      'X-Gateway-Route': session['scopes'][0]['route_fingerprint']}

    def causes(self):
        return self.client.get('/api/v1/scopes/desk/causes', headers=self.headers).json()

    def post(self, path, body):
        response = self.client.post('/api/v1/scopes/desk/' + path, headers=self.write, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    def prepare_freeze(self, request_id):
        view = self.causes()
        assert view['status'] == 'OBSERVED', view
        prepared = self.post('requests/prepare', {'request_id': request_id, 'mode': 'FREEZE', 'cause_id': '',
                                                  'expected_version': 0, 'view_receipt': view['scope_receipt']})
        assert prepared['status'] == 'PREPARED', prepared
        return prepared['receipt']

    def close(self):
        self.client.close()


def test_gateway_keeps_serving_while_the_web_host_starts_dies_and_restarts(tmp_path):
    with Gateway(tmp_path) as gateway:
        cli = gateway.runtime('cli')
        journal_id = cli_call(cli, 'cli', 'direct.control.identity', {}, gateway)['journal_id']
        client = OrdinaryClient(gateway, cli)
        client.start()
        token = 'c' * 64
        port = _free_port()
        origin = f'http://127.0.0.1:{port}'
        config = _write_protected(tmp_path / 'web-host.json', _host_config(gateway, 'account', port, token))
        log = tmp_path / 'web-host.log'
        processes = []
        try:
            first = _start_host(config, log)
            processes.append(first)
            _wait_ready(first, origin)
            browser = Browser(origin, token)
            applied = browser.post('requests/run', {'receipt': browser.prepare_freeze('web-process-1')})
            assert applied == {'status': 'OBSERVED', 'cause_id': 'web-process-1', 'version': 1, 'active': True}
            # The built-in host registers only the control mount: no account snapshot is offered.
            snapshot = browser.client.get('/api/v1/scopes/desk/snapshot', headers=browser.headers)
            assert (snapshot.status_code, snapshot.json()) == (
                403, {'detail': 'GATEWAY_SCOPE_OR_GRANT_UNAVAILABLE'})
            pending = browser.prepare_freeze('web-process-2')
            browser.close()

            # The Web host dies without any cleanup.
            first.send_signal(signal.SIGKILL)
            assert first.wait(10) == -signal.SIGKILL
            listed = cli_call(cli, 'cli', 'direct.control.list', {'after': '', 'limit': 10}, gateway)
            assert [(row['cause_id'], row['active']) for row in listed['causes']] == [('web-process-1', 1)]

            # A host that cannot get its grants fails to start and leaves the Gateway alone.
            denied = _write_protected(tmp_path / 'denied.json', _host_config(
                gateway, 'account', _free_port(), token, OPS | {'direct.control.targets'}))
            failed = _start_host(denied, tmp_path / 'denied.log')
            processes.append(failed)
            assert failed.wait(30) != 0
            assert 'GATEWAY_UNAVAILABLE' in (tmp_path / 'denied.log').read_text('utf-8', errors='replace')

            # Started again with the same protected file: earlier receipts still resolve, and the
            # request prepared before the crash is applied once. The killed host's registration
            # is still in the Registry until its lease expires; it does not stop the new one.
            before = _registrations(gateway)
            second = _start_host(config, log)
            processes.append(second)
            _wait_ready(second, origin)
            own = _registrations(gateway) - before
            assert len(own) == 1
            browser = Browser(origin, token)
            assert browser.post('requests/status', {'receipt': pending}) == {
                'status': 'NOT_FOUND', 'cause_id': 'web-process-2'}
            assert browser.post('requests/run', {'receipt': pending}) == {
                'status': 'OBSERVED', 'cause_id': 'web-process-2', 'version': 1, 'active': True}
            assert browser.post('requests/run', {'receipt': pending})['status'] == 'OBSERVED'
            browser.close()
            # A service manager's stop (SIGTERM) closes the host's own registration and exits 0.
            second.send_signal(signal.SIGTERM)
            assert second.wait(20) == 0
            assert not own & _registrations(gateway)
            assert cli_call(cli, 'cli', 'direct.control.identity', {}, gateway)['journal_id'] == journal_id
            # Ctrl-C closes the registration too.
            before = _registrations(gateway)
            third = _start_host(config, log)
            processes.append(third)
            _wait_ready(third, origin)
            own = _registrations(gateway) - before
            assert len(own) == 1
            third.send_signal(signal.SIGINT)
            third.wait(20)
            assert not own & _registrations(gateway)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(10)
            client.stop.set()
            client.join(10)
        # The ordinary client was served the whole time, by the same Gateway journal.
        assert len(client.results) > 10
        assert set(client.results) == {('ok', 'OBSERVED', journal_id)}
        assert cli_call(cli, 'cli', 'direct.control.identity', {}, gateway)['journal_id'] == journal_id
        assert gateway.on_owner(lambda: gateway.journal.journal_id) == journal_id
        causes = gateway.causes()
        assert set(causes) == {'web-process-1', 'web-process-2'}
        assert {row['origin'] for row in causes.values()} == {operator_origin('web-account-op', 'web-account-app')}


def _documented(heading):
    text = DOC.read_text('utf-8')
    start = text.index('\n## ' + heading + '\n')
    end = text.find('\n## ', start + 1)
    return text[start:] if end == -1 else text[start:end]


def test_reference_lists_the_built_in_host_routes(tmp_path):
    keys = zmq.curve_keypair(), zmq.curve_keypair()
    config = {'schema_version': 1, 'origin': 'http://127.0.0.1:8000',
              'receipt_key_base64': base64.b64encode(b'r' * 32).decode(), 'receipt_key_id': 'v1',
              'operators': [{'principal_ref': 'operator', 'actor_ref': 'app', 'login_token_sha256': 'a' * 64,
                             'scope_id': 'desk', 'scope_ref': 'scope', 'execution_domain_ref': DOMAIN,
                             'account_ref': ACCOUNT, 'strategy_ref': None, 'service_id': 'service',
                             'service_principal_ref': 'gateway', 'instance_id': 'gateway',
                             'endpoint': 'tcp://127.0.0.1:1', 'server_public_key': keys[0][0].decode(),
                             'client_public_key': keys[1][0].decode(), 'client_secret_key': keys[1][1].decode(),
                             'operations': sorted(OPS)}]}
    host = LocalGatewayWebHost(_write_protected(tmp_path / 'host.json', config))
    try:
        from fastapi.routing import APIRoute
        routes = {(method, route.path) for route in host.app.routes if isinstance(route, APIRoute)
                  for method in route.methods}
        documented = set(re.findall(r'^\| `([A-Z]+)` \| `(/[^`]*)` \|', _documented('8. Built-in host routes'),
                                    re.M))
        assert documented == {route for route in routes if not route[1].startswith('/api/v1/')}
        assert documented and all(not path.startswith('/api/v1/') for _, path in documented)
    finally:
        host.close()


def test_reference_lists_every_refusal_code_of_the_gateway_control_handler():
    codes = set(re.findall(r"_reject\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]\s*\)",
                           inspect.getsource(GatewayDirectServiceBinding._handle_control)))
    assert {'SCOPE_DENIED', 'CONTRACT_ORDERS_UNDECIDED', 'JOURNAL_ID_MISMATCH'} <= codes
    table = _documented('7. Status values and codes')
    assert sorted(code for code in codes if f'`{code}`' not in table) == []
