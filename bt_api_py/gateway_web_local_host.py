"""Local operator web host, with per-principal CURVE clients.

The protected file is an operator-controlled credential source, not a Gateway
grant. Every operation is still checked by the remote service registry. A
detectable change to that file invalidates browser sessions and requires a restart.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

_TOKEN = re.compile(r"^[a-f0-9]{64}$")
_OPERATIONS = frozenset({
    'direct.control.identity', 'direct.control.list', 'direct.control.status',
    'direct.control.targets', 'direct.control.target_status', 'direct.control.apply',
    'direct.control.resume', 'direct.control.resume_status', 'direct.control.drain_target',
})
# Optional second registration of an account-scope operator, on the Gateway's account read
# port: the account view of the snapshot route (it reads what the read model holds).
_ACCOUNT_OPERATIONS = frozenset({'account.snapshot.typed'})
_OPERATOR_FIELDS = ('principal_ref', 'actor_ref', 'login_token_sha256', 'scope_id', 'scope_ref',
                    'execution_domain_ref', 'account_ref', 'strategy_ref', 'service_id', 'service_principal_ref',
                    'instance_id', 'endpoint', 'server_public_key', 'client_public_key', 'client_secret_key',
                    'operations')


def _file_stamp(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_nlink, st.st_size,
            st.st_mtime_ns, st.st_ctime_ns)


def _protected_bytes(path: str | Path, limit: int = 131072) -> tuple[bytes, tuple]:
    """Read a regular, owner-only file without following any path symlink."""
    path = Path(path)
    if os.name != 'posix' or not path.is_absolute() or '..' in path.parts:
        raise ValueError('PROTECTED_FILE_UNAVAILABLE')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(file_fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or before.st_mode & 0o077 or before.st_nlink != 1 or before.st_size > limit):
                raise ValueError('PROTECTED_FILE_UNAVAILABLE')
            data = bytearray()
            while len(data) <= limit:
                block = os.read(file_fd, min(65536, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            after = os.fstat(file_fd)
            if len(data) > limit or _file_stamp(before) != _file_stamp(after):
                raise ValueError('PROTECTED_FILE_UNAVAILABLE')
            return bytes(data), _file_stamp(after)
        finally:
            os.close(file_fd)
    except (OSError, ValueError):
        raise ValueError('PROTECTED_FILE_UNAVAILABLE') from None
    finally:
        os.close(fd)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('CONFIG_INVALID')
        result[key] = value
    return result


def _fields(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError('CONFIG_INVALID')
    return value


def _secret_key(value):
    try:
        result = base64.b64decode(value, validate=True)
        if len(result) < 32 or len(result) > 64:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise ValueError('CONFIG_INVALID') from None


@dataclass(frozen=True)
class _Operator:
    token_hash: str = field(repr=False)
    identity: WireIdentity
    scope: WireScope
    web_scope: object
    runtime: ThreadedServiceClientRuntime = field(repr=False)


class LocalGatewayWebHost:
    """One local web process with separate current Gateway identities.

    Config contains credentials and must be an absolute owner-only file. Token
    hashes are for 32-byte random hexadecimal login tokens, not passwords.
    No operator or Gateway permissions are inferred from this file.
    """

    def __init__(self, config_path: str | Path, *, static_dir: str | Path | None = None):
        from bt_api_gateway.web.app import WebScope, create_app

        self._path = Path(config_path)
        raw, self._config_stamp = _protected_bytes(self._path)
        self._config_digest = hashlib.sha256(raw).digest()
        self._lock = threading.Lock()
        self._sessions = {}
        self._operators = []
        self._closed = False
        self._invalidated = False
        try:
            config = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
            _fields(config, ('schema_version', 'origin', 'receipt_key_base64', 'receipt_key_id', 'operators'))
            if (type(config['schema_version']) is not int or config['schema_version'] != 1
                    or type(config['operators']) is not list or not config['operators']):
                raise ValueError
            receipt_key = _secret_key(config['receipt_key_base64'])
            seen = set()
            for item in config['operators']:
                _fields(item, _OPERATOR_FIELDS + (('account_operations',) if type(item) is dict
                                                  and 'account_operations' in item else ()))
                if not _TOKEN.fullmatch(item['login_token_sha256']) or item['login_token_sha256'] in seen:
                    raise ValueError
                seen.add(item['login_token_sha256'])
                operations = item['operations']
                if (type(operations) is not list or not operations or len(set(operations)) != len(operations)
                        or not set(operations) <= _OPERATIONS):
                    raise ValueError
                account_operations = item.get('account_operations', [])
                if (type(account_operations) is not list or len(set(account_operations)) != len(account_operations)
                        or not set(account_operations) <= _ACCOUNT_OPERATIONS
                        or (account_operations and item['strategy_ref'] is not None)):
                    raise ValueError  # the account view is for an account-scope operator only
                scope = WireScope(item['execution_domain_ref'], account_ref=item['account_ref'],
                                  strategy_ref=item['strategy_ref'])
                identity = WireIdentity(WireRole.APPLICATION_CLIENT, item['principal_ref'], item['actor_ref'])
                peer = WireIdentity(WireRole.SERVICE, item['service_principal_ref'], item['service_id'])
                web_scope = WebScope(item['scope_id'], item['account_ref'], item['scope_ref'], item['strategy_ref'])
                import zmq
                server_key, public_key, secret_key = (item[name].encode('ascii') for name in
                    ('server_public_key', 'client_public_key', 'client_secret_key'))
                if any(len(key) != 40 for key in (server_key, public_key, secret_key)) or zmq.curve_public(secret_key) != public_key:
                    raise ValueError
                binding = {'mount': 'application.direct_write', 'service_id': item['service_id'],
                           'instance_id': item['instance_id'], 'scope_ref': item['scope_ref'],
                           'account_ref': item['account_ref'], 'transport_ref': 'gateway'}
                requested = {'application.direct_write': frozenset(operations)}
                bindings = [binding]
                if account_operations:
                    # Same Gateway, endpoint and keys; a separate registration the Registry
                    # grants or refuses on its own. Optional: when it is refused or lost, the
                    # control registration and the host go on (the snapshot route answers 403).
                    bindings.append({**binding, 'mount': 'application.account', 'required': False})
                    requested['application.account'] = frozenset(account_operations)
                client_config = parse_client_config({
                    'client_config_schema_version': 1, 'process_role': 'application_client',
                    'registration': {'application_ref': identity.actor_ref},
                    'clients': {'gateway': {'config_schema_version': 1, 'settings': {'bindings': bindings,
                        'options': {'request_timeout_ms': 2000}}}},
                    'communication': {'config_schema_version': 1, 'profiles': {'gateway': {
                        'backend': 'zmq', 'config_schema_version': 1,
                        'endpoints': {'rpc_connect': item['endpoint']}, 'security_profile_ref': 'operator-key'}}},
                })
                factory = ZmqTransportFactory({'operator-key': ClientCurveKeys(server_key, public_key, secret_key)})
                runtime = ThreadedServiceClientRuntime(lambda c=client_config, i=identity, s=scope, p=peer, f=factory, o=requested:
                    ServiceClientRuntime(c, identity=i, scope_resolver=lambda _: s, peer_resolver=lambda _: p,
                                         transport_factory=f, requested_operations=o))
                self._operators.append(_Operator(item['login_token_sha256'], identity, scope, web_scope, runtime))
            self.origin = config['origin']
            self.app = create_app(origin=self.origin, authenticate=self._authenticate,
                resolve_runtime=self._resolve_runtime, static_dir=static_dir,
                receipt_key=receipt_key, receipt_key_id=config['receipt_key_id'])
            self._install_login()
        except Exception:
            raise ValueError('LOCAL_GATEWAY_WEB_CONFIG_INVALID') from None

    def _current(self):
        try:
            raw, stamp = _protected_bytes(self._path)
            matches = (stamp == self._config_stamp
                       and hmac.compare_digest(hashlib.sha256(raw).digest(), self._config_digest))
        except ValueError:
            matches = False
        with self._lock:
            if not matches:
                self._invalidated = True
                self._sessions.clear()
            return not self._closed and not self._invalidated

    def start(self):
        try:
            for operator in self._operators:
                if not self._current():
                    raise ValueError('CONFIG_CHANGED')
                if not operator.runtime.start(timeout_seconds=10).healthy:
                    raise ValueError('GATEWAY_UNAVAILABLE')
            if not self._current():
                raise ValueError('CONFIG_CHANGED')
        except Exception:
            self.close()
            raise RuntimeError('GATEWAY_UNAVAILABLE') from None
        return self

    def close(self):
        self._closed = True
        with self._lock:
            self._sessions.clear()
        failed = False
        for operator in self._operators:
            try:
                result = operator.runtime.close(timeout_seconds=10)
                if result.thread_alive or result.cleanup_failures or result.unregister_uncertainties:
                    failed = True
            except Exception:
                failed = True
        if failed:
            raise RuntimeError('GATEWAY_CLEANUP_UNCERTAIN')

    def _authenticate(self, request):
        from bt_api_gateway.web.app import AuthenticatedWebSession
        token = request.headers.get('x-gateway-session', '')
        if not _TOKEN.fullmatch(token) or not self._current():
            raise ValueError('AUTHENTICATION_REQUIRED')
        with self._lock:
            entry = self._sessions.get(hashlib.sha256(token.encode()).digest())
            if entry is None or time.monotonic() >= entry[2]:
                raise ValueError('AUTHENTICATION_REQUIRED')
            operator, csrf, _ = entry
        return AuthenticatedWebSession(operator.identity.principal_ref, operator.identity.actor_ref,
                                       csrf, (operator.web_scope,))

    def _resolve_runtime(self, session, scope):
        if not self._current():
            raise ValueError('AUTHENTICATION_REQUIRED')
        matches = [operator for operator in self._operators if operator.identity.principal_ref == session.principal_ref
                   and operator.identity.actor_ref == session.actor_ref and operator.web_scope == scope]
        if len(matches) != 1:
            raise ValueError('GATEWAY_SCOPE_UNAVAILABLE')
        return matches[0].runtime

    def _install_login(self):
        from fastapi import Request
        from fastapi.responses import HTMLResponse, JSONResponse, Response

        async def form():
            return HTMLResponse('<!doctype html><html><head><title>Gateway login</title></head><body>'
                '<form id="login" method="post" action="/login"><label>Operator token '
                '<input type="password" name="token" autocomplete="off" required></label>'
                '<button type="submit">Sign in</button></form><p id="error"></p>'
                '<script src="/gateway-local-login.js" defer></script></body></html>')

        async def login_script():
            return Response("""'use strict';
document.getElementById('login').addEventListener('submit', async function (event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const body = new URLSearchParams(new FormData(form));
    const response = await fetch('/login', { method: 'POST', body, credentials: 'omit', cache: 'no-store' });
    const result = await response.json();
    if (!response.ok || !/^[a-f0-9]{64}$/.test(result.session)) throw new Error('login');
    sessionStorage.setItem('gateway.local.session.v1', result.session);
    form.reset();
    location.replace('/');
  } catch (_) { form.reset(); document.getElementById('error').textContent = 'Authentication unavailable'; }
});
""", media_type='application/javascript')

        async def logout_form():
            return HTMLResponse('<!doctype html><html><head><title>Gateway logout</title></head><body>'
                'Signing out...<script src="/gateway-local-logout.js" defer></script></body></html>')

        async def logout_script():
            return Response("""'use strict';
(async function () {
  try {
    const token = sessionStorage.getItem('gateway.local.session.v1');
    if (token) await fetch('/logout', {method: 'POST', headers: {'X-Gateway-Session': token},
      credentials: 'omit', cache: 'no-store'});
  } finally { sessionStorage.removeItem('gateway.local.session.v1'); location.replace('/login'); }
})();
""", media_type='application/javascript')

        async def logout(request: Request):
            token = request.headers.get('x-gateway-session', '')
            if not _TOKEN.fullmatch(token):
                return JSONResponse({'status': 'UNAVAILABLE'}, status_code=401)
            with self._lock:
                self._sessions.pop(hashlib.sha256(token.encode()).digest(), None)
            return JSONResponse({'status': 'SIGNED_OUT'})

        async def login(request: Request):
            if not self._current():
                return HTMLResponse('Authentication unavailable', status_code=401)
            try:
                form_data = parse_qs((await request.body()).decode('ascii'), strict_parsing=True)
                token = form_data['token'][0]
                if set(form_data) != {'token'} or len(form_data['token']) != 1 or not _TOKEN.fullmatch(token):
                    raise ValueError
                digest = hashlib.sha256(token.encode('ascii')).hexdigest()
                matches = [operator for operator in self._operators if hmac.compare_digest(operator.token_hash, digest)]
                if len(matches) != 1:
                    raise ValueError
            except (UnicodeError, ValueError, KeyError):
                return HTMLResponse('Authentication failed', status_code=401)
            session_token, csrf = secrets.token_hex(32), secrets.token_hex(32)
            with self._lock:
                now = time.monotonic()
                self._sessions = {key: value for key, value in self._sessions.items() if value[2] > now}
                if len(self._sessions) >= 128:
                    return HTMLResponse('Session capacity exceeded', status_code=503)
                self._sessions[hashlib.sha256(session_token.encode()).digest()] = (matches[0], csrf, now + 1800)
            return JSONResponse({'session': session_token})

        # StaticFiles is mounted at /; place login before it without changing the web adapter.
        from fastapi.routing import APIRoute
        login.__annotations__['request'] = Request
        logout.__annotations__['request'] = Request
        self.app.router.routes.insert(0, APIRoute('/login', form, methods=['GET']))
        self.app.router.routes.insert(1, APIRoute('/login', login, methods=['POST']))
        self.app.router.routes.insert(2, APIRoute('/gateway-local-login.js', login_script, methods=['GET']))
        self.app.router.routes.insert(3, APIRoute('/logout', logout_form, methods=['GET']))
        self.app.router.routes.insert(4, APIRoute('/logout', logout, methods=['POST']))
        self.app.router.routes.insert(5, APIRoute('/gateway-local-logout.js', logout_script, methods=['GET']))


def _stop_on_terminate(_signum, _frame):
    # uvicorn stops serving on SIGTERM and then raises the signal again. Leave through
    # SystemExit, as Ctrl-C does through KeyboardInterrupt, so that main() still closes
    # this host's own Gateway registrations instead of leaving them to expire.
    raise SystemExit(0)


def main():
    from bt_api_gateway.web.app import bundled_static_dir, run_local
    parser = argparse.ArgumentParser(description='Local Gateway web operator host')
    parser.add_argument('--config', required=True, help='Absolute path to owner-only local JSON')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _stop_on_terminate)
    host = LocalGatewayWebHost(args.config, static_dir=bundled_static_dir())
    try:
        host.start()
        run_local(host.app, origin=host.origin)
    finally:
        host.close()


if __name__ == '__main__':
    main()
