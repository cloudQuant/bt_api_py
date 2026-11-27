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
import socket
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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
_ACCOUNT_OPERATIONS = frozenset({'account.snapshot.typed', 'account.executions.recent'})
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
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('PROTECTED_FILE_UNAVAILABLE')
    if os.name == 'nt':
        from bt_api_py._win32_security import (
            Win32SecurityUnavailable,
            read_bounded_windows_with_stamp,
        )

        try:
            return read_bounded_windows_with_stamp(path, limit, protected=True)
        except (Win32SecurityUnavailable, OSError, ValueError):
            raise ValueError('PROTECTED_FILE_UNAVAILABLE') from None
    if os.name != 'posix':
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


def _endpoint_listening(endpoint: str, *, timeout: float = 0.25) -> bool:
    """Non-mutating TCP reachability check before waking a parked unregister."""
    if not isinstance(endpoint, str) or not endpoint.startswith('tcp://'):
        return True
    parsed = urlsplit(endpoint)
    host = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        return True
    if not host or port is None:
        return True
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _cleanup_confirmed_for_rotation(closed, *, retired_bindings=()) -> bool:
    """Return True when a closed runtime has no unproven remote registrations.

    With ``require_unregister_ack=True``, a restarted server rejects the old
    ``(registration_id, service_epoch)`` with server-authenticated
    ``STALE_EPOCH`` / ``NOT_REGISTERED`` rather than ``unregistered=True``.
    Permit replacement/closure only when every unresolved registration id is
    backed by that exact server-authenticated inactive proof (never timeouts
    or transport errors) and zero local transports or unknown registrations
    remain open.
    """
    if getattr(closed, 'thread_alive', True) is not False:
        return False
    if getattr(closed, 'state', None) not in ('closed', 'failed'):
        return False
    if getattr(closed, 'open_transport_count', 0) not in (None, 0):
        return False
    if getattr(closed, 'unknown_registration_count', 0) not in (None, 0):
        return False
    if not closed.cleanup_failures and not closed.unregister_uncertainties:
        return True
    unresolved = getattr(closed, 'unresolved_registration_ids', None) or ()
    remote_inactive = dict(getattr(closed, 'remote_inactive_registrations', None) or ())
    if not unresolved or not remote_inactive:
        return False
    if len(unresolved) != closed.unregister_uncertainties:
        return False
    if closed.cleanup_failures > len(unresolved):
        return False
    epoch_by_reg = {
        b.registration_id: b.service_epoch
        for b in retired_bindings
        if getattr(b, 'registration_id', None) and getattr(b, 'service_epoch', None)
    }
    return all(
        reg_id in remote_inactive
        and (not epoch_by_reg or (reg_id in epoch_by_reg and remote_inactive[reg_id] == epoch_by_reg[reg_id]))
        for reg_id in unresolved
    )


@dataclass
class _Operator:
    token_hash: str = field(repr=False)
    identity: WireIdentity
    scope: WireScope
    web_scope: object
    runtime: ThreadedServiceClientRuntime = field(repr=False)
    runtime_factory: object = field(repr=False)
    endpoint: str = ''
    retired_bindings: tuple = ()
    reconnect_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


def _runtime_usable(runtime: ThreadedServiceClientRuntime) -> bool:
    """Return True only when the runtime is running, healthy, and peer-proven."""
    try:
        state = runtime.status()
    except Exception:
        return False
    if state.state != 'running' or not state.thread_alive or not state.healthy:
        return False
    remaining = getattr(state, 'min_socket_message_ids_remaining', None)
    if type(remaining) is int and remaining <= 32:
        return False
    active_bindings = [b for b in state.bindings if b.active]
    if not active_bindings:
        return False
    remote_inactive = getattr(state, 'remote_inactive_registrations', None) or ()
    if remote_inactive and any(
        (b.registration_id, b.service_epoch) in remote_inactive
        for b in active_bindings
    ):
        return False
    return all(
        b.authenticated_peer is not None and b.server_key_fingerprint is not None
        for b in active_bindings
    )


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
        self._started = False
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
                        'options': {'request_timeout_ms': 5000}}}},
                    'communication': {'config_schema_version': 1, 'profiles': {'gateway': {
                        'backend': 'zmq', 'config_schema_version': 1,
                        'endpoints': {'rpc_connect': item['endpoint']}, 'security_profile_ref': 'operator-key'}}},
                })
                factory = ZmqTransportFactory({'operator-key': ClientCurveKeys(server_key, public_key, secret_key)})
                runtime_factory = (
                    lambda c=client_config, i=identity, s=scope, p=peer, f=factory, o=requested:
                    ThreadedServiceClientRuntime(
                        lambda c=c, i=i, s=s, p=p, f=f, o=o: ServiceClientRuntime(
                            c, identity=i, scope_resolver=lambda _: s, peer_resolver=lambda _: p,
                            transport_factory=f, requested_operations=o,
                            require_unregister_ack=True,
                        ),
                        require_unregister_ack=True,
                        defer_cleanup_until_close=True,
                    )
                )
                runtime = runtime_factory()
                self._operators.append(
                    _Operator(
                        item['login_token_sha256'], identity, scope, web_scope,
                        runtime, runtime_factory, endpoint=item['endpoint'],
                    )
                )
            self.origin = config['origin']
            self.app = create_app(origin=self.origin, authenticate=self._authenticate,
                resolve_runtime=self._resolve_runtime, static_dir=static_dir,
                receipt_key=receipt_key, receipt_key_id=config['receipt_key_id'],
                timeout_seconds=4.0)
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
            self._started = True
        except Exception:
            self.close()
            raise RuntimeError('GATEWAY_UNAVAILABLE') from None
        return self

    def close(self):
        self._closed = True
        self._started = False
        with self._lock:
            self._sessions.clear()
        failed = False
        for operator in self._operators:
            with operator.reconnect_lock:
                try:
                    before = operator.runtime.status()
                    retired = before.bindings or operator.retired_bindings
                    result = operator.runtime.close(timeout_seconds=10)
                    if not _cleanup_confirmed_for_rotation(result, retired_bindings=retired):
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

    def _ensure_operator_runtime(self, operator: _Operator) -> ThreadedServiceClientRuntime:
        current = operator.runtime
        if _runtime_usable(current) or not self._started or self._closed:
            return current
        with operator.reconnect_lock:
            current = operator.runtime
            if _runtime_usable(current) or not self._started or not self._current():
                return current
            # Cleanly unregister and retire the dead/socket-retired runtime before
            # creating a fresh CURVE registration; fail closed if cleanup is uncertain.
            try:
                st = current.status()
                if st.bindings:
                    operator.retired_bindings = st.bindings
                if st.state == 'closed' and not st.thread_alive:
                    closed = st
                elif (
                    st.state == 'failed'
                    and st.thread_alive
                    and not _endpoint_listening(operator.endpoint)
                ):
                    return current
                else:
                    closed = current.close(timeout_seconds=10)
            except Exception:
                return current
            if not _cleanup_confirmed_for_rotation(closed, retired_bindings=operator.retired_bindings):
                return current
            new_runtime = operator.runtime_factory()
            try:
                started = new_runtime.start(timeout_seconds=10)
                if not started.healthy or not _runtime_usable(new_runtime) or not self._current():
                    try:
                        new_runtime.close(timeout_seconds=10)
                    except Exception:
                        pass
                    return current
            except Exception:
                try:
                    new_runtime.close(timeout_seconds=10)
                except Exception:
                    pass
                return current
            operator.retired_bindings = ()
            operator.runtime = new_runtime
            return new_runtime

    def _resolve_runtime(self, session, scope):
        if not self._current():
            raise ValueError('AUTHENTICATION_REQUIRED')
        matches = [operator for operator in self._operators if operator.identity.principal_ref == session.principal_ref
                   and operator.identity.actor_ref == session.actor_ref and operator.web_scope == scope]
        if len(matches) != 1:
            raise ValueError('GATEWAY_SCOPE_UNAVAILABLE')
        return self._ensure_operator_runtime(matches[0])

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


def _run_local_with_stop_file(app, *, origin: str, stop_file: Path) -> None:
    """Serve on loopback and stop cooperatively when the protected empty marker appears."""
    from urllib.parse import urlsplit
    import uvicorn
    from bt_api_gateway.web.app import _local_origin
    from bt_api_py._owner_stop_file import marker_requested

    _local_origin(origin)
    parsed = urlsplit(origin)
    if parsed.scheme != 'http' or parsed.port is None:
        raise ValueError('LOCAL_HTTP_PORT_REQUIRED')
    bind_host = '127.0.0.1' if parsed.hostname == 'localhost' else parsed.hostname
    server = uvicorn.Server(
        uvicorn.Config(app, host=bind_host, port=parsed.port, proxy_headers=False)
    )
    done = threading.Event()

    def _watch() -> None:
        while not done.wait(0.1):
            if marker_requested(stop_file):
                server.should_exit = True
                return

    watcher = threading.Thread(target=_watch, name='gateway-web-stop-watcher', daemon=True)
    watcher.start()
    try:
        server.run()
    finally:
        done.set()
        watcher.join(timeout=1.0)


def main(argv=None):
    from bt_api_gateway.web.app import bundled_static_dir, run_local
    parser = argparse.ArgumentParser(description='Local Gateway web operator host')
    parser.add_argument('--config', required=True, help='Absolute path to owner-only local JSON')
    parser.add_argument(
        '--enable-owner-stop-file',
        action='store_true',
        help='allow local cooperative shutdown with a protected empty marker beside the configuration',
    )
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _stop_on_terminate)
    host = LocalGatewayWebHost(args.config, static_dir=bundled_static_dir())
    try:
        host.start()
        if args.enable_owner_stop_file:
            from bt_api_py._owner_stop_file import new_marker

            stop_file = new_marker(Path(args.config).resolve().parent, 'gateway-web')
            print('GATEWAY_WEB_STOP_FILE ' + stop_file.name, flush=True)
            _run_local_with_stop_file(host.app, origin=host.origin, stop_file=stop_file)
        else:
            run_local(host.app, origin=host.origin)
    finally:
        host.close()
    if args.enable_owner_stop_file:
        print('GATEWAY_WEB_STOPPED', flush=True)


if __name__ == '__main__':
    main()
