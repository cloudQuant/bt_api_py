"""Local operator Web host of the independent Risk service (iteration 64, step 64.13 interface).

One host process serves one operator principal. It registers that principal, with its
own CURVE keys, on the Risk service's operator port (mount ``application.risk_admin``)
and serves ``bt_api_risk.web.app`` on 127.0.0.1 only. The protected configuration file
is an operator-controlled credential source, not a Risk grant: the Risk service's
Registry and handlers authorize, validate, execute and audit every call.

Starting, stopping or killing this host opens or closes only its own registration. It
never changes the Risk service, its policy, latches, permits or its route to the
Gateway, and it never sends an order operation. A detectable change of the
configuration file ends every browser session until the host is restarted.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import signal
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bt_api_base.risk_service_protocol import RISK_ADMIN_OPERATIONS
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_web_local_host import _protected_bytes, _unique_object
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import (
    BoundCallObservation, BoundCallResult, BoundCallRoute,
    ThreadedRuntimeError, ThreadedServiceClientRuntime,
)

RISK_ADMIN_MOUNT = "application.risk_admin"
SESSION_HEADER = "x-risk-session"
SESSION_SECONDS = 1800
MAX_SESSIONS = 128
_TOKEN = re.compile(r"[a-f0-9]{64}")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_CONFIG_FIELDS = frozenset({"schema_version", "origin", "operator"})
_OPERATOR_FIELDS = frozenset(
    {
        "principal_ref",
        "actor_ref",
        "login_token_sha256",
        "execution_domain_ref",
        "account_ref",
        "scope_ref",
        "service_id",
        "service_principal_ref",
        "instance_id",
        "endpoint",
        "server_public_key",
        "client_public_key",
        "client_secret_key",
        "operations",
    }
)
# The threaded runtime proves these never started (mirrors risk_service_client).
_NOT_STARTED_ERRORS = frozenset(
    {
        "NOT_READY",
        "QUEUE_FULL",
        "TIMEOUT_BEFORE_START",
        "CLOSED_BEFORE_START",
        "WRONG_PROCESS",
        "OWNER_THREAD_REENTRY",
        "INVALID_ARGUMENTS",
        "BOUND_ROUTE_UNAVAILABLE",
    }
)
def _ref(value: object) -> bool:
    if type(value) is not str or not value or value != value.strip() or not value.isprintable():
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 256
    except UnicodeError:
        return False


def _timeout(value: object) -> bool:
    return type(value) in (int, float) and 0 < value <= 30


def _scope_key(value: object) -> tuple:
    """Copy and revalidate every scope field; the values are claims, not authority."""
    if type(value) is not WireScope or type(value.account_refs) is not tuple:
        raise ValueError("ROUTE_UNAVAILABLE")
    copied = WireScope(
        value.execution_domain_ref, value.account_ref, value.account_refs,
        value.strategy_ref, value.run_id, value.manual_book_ref,
        value.portfolio_ref, value.group_id, value.leg_id,
    )
    return (copied.execution_domain_ref, copied.account_ref, copied.account_refs,
            copied.strategy_ref, copied.run_id, copied.manual_book_ref,
            copied.portfolio_ref, copied.group_id, copied.leg_id)


def _identity_key(value: object) -> tuple:
    if type(value) is not WireIdentity:
        raise ValueError("ROUTE_UNAVAILABLE")
    copied = WireIdentity(value.role, value.principal_ref, value.actor_ref, value.delegate_ref)
    return copied.role, copied.principal_ref, copied.actor_ref, copied.delegate_ref


def loopback_origin(origin: object) -> tuple[str, int]:
    """``http://127.0.0.1:<port>`` only; any other host, scheme or form is refused."""
    if type(origin) is not str:
        raise ValueError("LOOPBACK_ORIGIN_REQUIRED")
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or origin.rstrip("/") != f"http://127.0.0.1:{port}"
    ):
        raise ValueError("LOOPBACK_ORIGIN_REQUIRED")
    return "127.0.0.1", port


def _risk_runtime_usable(runtime: Any) -> bool:
    """Return True only when the operator runtime is running, healthy, and peer-proven."""
    try:
        state = runtime.status()
    except Exception:
        return False
    if state.state != "running" or not state.thread_alive or not state.healthy:
        return False
    remaining = getattr(state, "min_socket_message_ids_remaining", None)
    if type(remaining) is int and remaining <= 32:
        return False
    active_bindings = [b for b in state.bindings if b.active]
    if not active_bindings:
        return False
    return all(
        b.authenticated_peer is not None and b.server_key_fingerprint is not None
        for b in active_bindings
    )


def build_admin_caller(
    runtime: Any,
    *,
    identity: WireIdentity,
    scope: WireScope,
    scope_ref: str,
    service_id: str,
    instance_id: str,
    peer: WireIdentity,
    runtime_resolver: Callable[[], Any] | None = None,
) -> Callable[[str, dict, float], Mapping]:
    """The Web adapter's admin caller over one operator registration; it never resends.

    Only ``RISK_ADMIN_OPERATIONS`` are sent, only on ``application.risk_admin``, only
    while the runtime holds exactly one active registration of this operator for this
    account on the configured Risk service and authenticated peer. Failures are raised
    as ``RiskAdminCallError`` with a finite outcome class (NOT_SENT, REFUSED, TIMEOUT,
    UNKNOWN); no exception text or value leaves this function.
    """
    from bt_api_risk.web.app import RiskAdminCallError

    if (
        type(identity) is not WireIdentity
        or identity.role is not WireRole.APPLICATION_CLIENT
        or identity.delegate_ref is not None
        or type(peer) is not WireIdentity
        or peer.role is not WireRole.SERVICE
        or peer.delegate_ref is not None
        or type(scope) is not WireScope
        or scope.account_ref is None
        or scope.strategy_ref is not None
        or not all(_ref(value) for value in (scope_ref, service_id, instance_id))
        or not callable(getattr(runtime, "call_bound", None))
    ):
        raise ValueError("INVALID_RISK_ADMIN_ROUTE")
    try:
        expected_scope = _scope_key(scope)
        # This host's configuration grants exactly one account, with no
        # strategy, group, portfolio or manual-book scope.
        if expected_scope != _scope_key(WireScope(scope.execution_domain_ref, account_ref=scope.account_ref)):
            raise ValueError
        expected_identity = _identity_key(identity)
        expected_peer = _identity_key(peer)
        route = BoundCallRoute(identity, scope, scope_ref, service_id, instance_id, peer)
    except Exception:
        raise ValueError("INVALID_RISK_ADMIN_ROUTE") from None
    pid = os.getpid()

    def call(operation: str, arguments: dict, timeout_seconds: float) -> Mapping:
        # Order operations (risk.order.*, risk.parent.*) are never sent from here.
        if (
            type(operation) is not str
            or operation not in RISK_ADMIN_OPERATIONS
            or type(arguments) is not dict
            or not _timeout(timeout_seconds)
        ):
            raise RiskAdminCallError("NOT_SENT", "INVALID_ARGUMENTS")
        if os.getpid() != pid:
            raise RiskAdminCallError("NOT_SENT", "WRONG_PROCESS")
        active_runtime = runtime_resolver() if runtime_resolver is not None else runtime
        try:
            bound = active_runtime.call_bound(
                RISK_ADMIN_MOUNT,
                operation,
                arguments,
                route=route,
                timeout_seconds=timeout_seconds,
            )
        except ThreadedRuntimeError as error:
            code = error.code
            if type(code) is not str:
                raise RiskAdminCallError("UNKNOWN") from None
            if code in _NOT_STARTED_ERRORS:
                raise RiskAdminCallError("NOT_SENT", "ROUTE_UNAVAILABLE" if code == "BOUND_ROUTE_UNAVAILABLE" else code) from None
            raise RiskAdminCallError("UNKNOWN") from None
        except Exception:
            raise RiskAdminCallError("UNKNOWN") from None
        try:
            if (type(bound) is not BoundCallResult or type(bound.before) is not BoundCallObservation
                    or type(bound.after) is not BoundCallObservation or bound.before != bound.after
                    or _identity_key(bound.before.identity) != expected_identity
                    or _identity_key(bound.before.peer) != expected_peer
                    or _scope_key(bound.before.scope) != expected_scope
                    or bound.before.mount != RISK_ADMIN_MOUNT or bound.before.service_id != service_id
                    or bound.before.instance_id != instance_id or bound.before.scope_ref != scope_ref
                    or bound.before.active is not True or bound.before.required is not True
                    or bound.before.operation != operation
                    or type(bound.before.operations) is not frozenset or operation not in bound.before.operations
                    or not _ref(bound.before.run_id) or not _ref(bound.before.registration_id)
                    or not _ref(bound.before.service_epoch) or type(bound.before.fingerprint) is not str
                    or _FINGERPRINT.fullmatch(bound.before.fingerprint) is None
                    or type(bound.before.lease_deadline) not in (int, float)
                    or not math.isfinite(bound.before.lease_deadline) or bound.before.lease_deadline <= 0
                    or not isinstance(bound.result, Mapping)):
                raise ValueError
        except Exception:
            raise RiskAdminCallError("UNKNOWN") from None
        return bound.result

    return call


def _fields(value: object, expected: frozenset) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError("CONFIG_INVALID")
    return value


class LocalRiskWebHost:
    """One local Web process for one operator principal of one Risk service.

    The configuration is an absolute, owner-only JSON file (it holds the operator's
    CURVE secret key). ``login_token_sha256`` is the SHA-256 of a 32-byte random
    hexadecimal login token, not of a password. No Risk permission is inferred from
    this file; ``operations`` only narrows what this host may send.
    """

    def __init__(self, config_path: str | Path):
        from bt_api_risk.web.app import AuthenticatedRiskSession, create_app

        self._session_type = AuthenticatedRiskSession
        self._path = Path(config_path)
        raw, self._config_stamp = _protected_bytes(self._path)
        self._config_digest = hashlib.sha256(raw).digest()
        self._lock = threading.Lock()
        self._reconnect_lock = threading.Lock()
        self._sessions: dict[bytes, tuple[str, float]] = {}
        self._closed = False
        self._invalidated = False
        self._started = False
        try:
            config = _fields(
                json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object), _CONFIG_FIELDS
            )
            if type(config["schema_version"]) is not int or config["schema_version"] != 1:
                raise ValueError
            loopback_origin(config["origin"])
            item = _fields(config["operator"], _OPERATOR_FIELDS)
            if type(item["login_token_sha256"]) is not str or not _TOKEN.fullmatch(
                item["login_token_sha256"]
            ):
                raise ValueError
            operations = item["operations"]
            if (
                type(operations) is not list
                or not operations
                or len(set(operations)) != len(operations)
                or not set(operations) <= RISK_ADMIN_OPERATIONS
            ):
                raise ValueError
            names = (
                "principal_ref",
                "actor_ref",
                "execution_domain_ref",
                "account_ref",
                "scope_ref",
                "service_id",
                "service_principal_ref",
                "instance_id",
            )
            if not all(_ref(item[name]) for name in names) or type(item["endpoint"]) is not str:
                raise ValueError
            identity = WireIdentity(
                WireRole.APPLICATION_CLIENT, item["principal_ref"], item["actor_ref"]
            )
            peer = WireIdentity(WireRole.SERVICE, item["service_principal_ref"], item["service_id"])
            scope = WireScope(item["execution_domain_ref"], account_ref=item["account_ref"])
            import zmq

            keys = tuple(
                item[name].encode("ascii") if type(item[name]) is str else b""
                for name in ("server_public_key", "client_public_key", "client_secret_key")
            )
            if any(len(key) != 40 for key in keys) or zmq.curve_public(keys[2]) != keys[1]:
                raise ValueError
            client_config = parse_client_config(
                {
                    "client_config_schema_version": 1,
                    "process_role": "application_client",
                    "registration": {"application_ref": identity.actor_ref},
                    "clients": {
                        "risk": {
                            "config_schema_version": 1,
                            "settings": {
                                "bindings": [
                                    {
                                        "mount": RISK_ADMIN_MOUNT,
                                        "service_id": item["service_id"],
                                        "instance_id": item["instance_id"],
                                        "scope_ref": item["scope_ref"],
                                        "account_ref": item["account_ref"],
                                        "transport_ref": "risk",
                                    }
                                ],
                                "options": {"request_timeout_ms": 5000},
                            },
                        }
                    },
                    "communication": {
                        "config_schema_version": 1,
                        "profiles": {
                            "risk": {
                                "backend": "zmq",
                                "config_schema_version": 1,
                                "endpoints": {"rpc_connect": item["endpoint"]},
                                "security_profile_ref": "risk-operator-key",
                            }
                        },
                    },
                }
            )
            factory = ZmqTransportFactory({"risk-operator-key": ClientCurveKeys(*keys)})
            requested = {RISK_ADMIN_MOUNT: frozenset(operations)}
            scope_ref, service_id = item["scope_ref"], item["service_id"]

            def scope_resolver(ref: str) -> WireScope:
                if ref != scope_ref:
                    raise ValueError("INVALID_SCOPE")
                return scope

            def peer_resolver(ref: str) -> WireIdentity:
                if ref != service_id:
                    raise ValueError("INVALID_PEER")
                return peer

            self._runtime_factory = lambda: ThreadedServiceClientRuntime(
                lambda: ServiceClientRuntime(
                    client_config,
                    identity=identity,
                    scope_resolver=scope_resolver,
                    peer_resolver=peer_resolver,
                    transport_factory=factory,
                    requested_operations=requested,
                    require_unregister_ack=True,
                ),
                require_unregister_ack=True,
            )
            self._runtime = self._runtime_factory()
            self._identity = identity
            self._operations = frozenset(operations)
            self._token_hash = item["login_token_sha256"]
            caller = build_admin_caller(
                self._runtime,
                identity=identity,
                scope=scope,
                scope_ref=scope_ref,
                service_id=service_id,
                instance_id=item["instance_id"],
                peer=peer,
                runtime_resolver=self._ensure_runtime,
            )
            self.origin = config["origin"].rstrip("/")
            self.app = create_app(
                origin=self.origin, authenticate=self._authenticate, admin_caller=caller
            )
            self._install_login()
        except Exception:
            raise ValueError("LOCAL_RISK_WEB_CONFIG_INVALID") from None

    def _current(self) -> bool:
        try:
            raw, stamp = _protected_bytes(self._path)
            matches = stamp == self._config_stamp and hmac.compare_digest(
                hashlib.sha256(raw).digest(), self._config_digest
            )
        except ValueError:
            matches = False
        with self._lock:
            if not matches:
                self._invalidated = True
                self._sessions.clear()
            return not self._closed and not self._invalidated

    def start(self) -> LocalRiskWebHost:
        try:
            if not self._current():
                raise ValueError("CONFIG_CHANGED")
            if not self._runtime.start(timeout_seconds=10).healthy:
                raise ValueError("RISK_ADMIN_UNAVAILABLE")
            if not self._current():
                raise ValueError("CONFIG_CHANGED")
            self._started = True
        except Exception:
            try:
                self.close()
            except RuntimeError:
                raise RuntimeError("RISK_ADMIN_UNAVAILABLE_CLEANUP_UNCERTAIN") from None
            raise RuntimeError("RISK_ADMIN_UNAVAILABLE") from None
        return self

    def _ensure_runtime(self) -> Any:
        current = self._runtime
        if _risk_runtime_usable(current) or not self._started or self._closed:
            return current
        with self._reconnect_lock:
            current = self._runtime
            if _risk_runtime_usable(current) or not self._started or not self._current():
                return current
            # Cleanly unregister and close the retired/unhealthy runtime before
            # creating a fresh CURVE registration; fail closed on uncertain cleanup.
            try:
                st = current.status()
                if st.state == "closed" and not st.thread_alive:
                    closed = st
                else:
                    closed = current.close(timeout_seconds=10)
            except Exception:
                return current
            if closed.thread_alive or closed.cleanup_failures or closed.unregister_uncertainties:
                return current
            new_runtime = self._runtime_factory()
            try:
                started = new_runtime.start(timeout_seconds=10)
                if not started.healthy or not _risk_runtime_usable(new_runtime) or not self._current():
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
            self._runtime = new_runtime
            return new_runtime

    def close(self) -> None:
        """Close this host's own registration; the Risk service keeps running unchanged."""
        self._closed = True
        self._started = False
        with self._lock:
            self._sessions.clear()
        with self._reconnect_lock:
            try:
                result = self._runtime.close(timeout_seconds=10)
                failed = bool(
                    result.thread_alive or result.cleanup_failures or result.unregister_uncertainties
                )
            except Exception:
                failed = True
        if failed:
            raise RuntimeError("RISK_WEB_CLEANUP_UNCERTAIN")

    def _authenticate(self, request):
        token = request.headers.get(SESSION_HEADER, "")
        if not _TOKEN.fullmatch(token) or not self._current():
            raise ValueError("AUTHENTICATION_REQUIRED")
        with self._lock:
            entry = self._sessions.get(hashlib.sha256(token.encode("ascii")).digest())
            if entry is None or time.monotonic() >= entry[1]:
                raise ValueError("AUTHENTICATION_REQUIRED")
            csrf = entry[0]
        return self._session_type(
            self._identity.principal_ref, self._identity.actor_ref, csrf, self._operations
        )

    def _install_login(self) -> None:
        from fastapi import Request
        from fastapi.responses import JSONResponse

        async def login(request: Request):
            if not self._current():
                return JSONResponse({"error": "AUTHENTICATION_REQUIRED"}, status_code=401)
            try:
                form = parse_qs((await request.body()).decode("ascii"), strict_parsing=True)
                if set(form) != {"token"} or len(form["token"]) != 1:
                    raise ValueError
                token = form["token"][0]
                if not _TOKEN.fullmatch(token):
                    raise ValueError
                digest = hashlib.sha256(token.encode("ascii")).hexdigest()
                if not hmac.compare_digest(digest, self._token_hash):
                    raise ValueError
            except (UnicodeError, ValueError, KeyError):
                return JSONResponse({"error": "AUTHENTICATION_REQUIRED"}, status_code=401)
            session_token, csrf = secrets.token_hex(32), secrets.token_hex(32)
            with self._lock:
                now = time.monotonic()
                self._sessions = {k: v for k, v in self._sessions.items() if v[1] > now}
                if len(self._sessions) >= MAX_SESSIONS:
                    return JSONResponse({"error": "SESSION_CAPACITY"}, status_code=503)
                self._sessions[hashlib.sha256(session_token.encode("ascii")).digest()] = (
                    csrf,
                    now + SESSION_SECONDS,
                )
            return JSONResponse({"session": session_token})

        async def logout(request: Request):
            token = request.headers.get(SESSION_HEADER, "")
            if not _TOKEN.fullmatch(token):
                return JSONResponse({"error": "AUTHENTICATION_REQUIRED"}, status_code=401)
            with self._lock:
                self._sessions.pop(hashlib.sha256(token.encode("ascii")).digest(), None)
            return JSONResponse({"status": "SIGNED_OUT"})

        # Annotations are strings here; FastAPI resolves them in this module's globals.
        login.__annotations__["request"] = Request
        logout.__annotations__["request"] = Request
        self.app.add_api_route("/login", login, methods=["POST"])
        self.app.add_api_route("/logout", logout, methods=["POST"])


def _stop_on_terminate(_signum, _frame):
    # Leave through SystemExit so that main() still closes this host's own registration.
    raise SystemExit(0)


def main(argv: list[str] | None = None) -> None:
    """``python -m bt_api_py.risk_web_local_host --config <absolute owner-only JSON>``."""
    from bt_api_risk.web.app import run_local

    parser = argparse.ArgumentParser(description="Local Risk service operator Web API host")
    parser.add_argument("--config", required=True, help="Absolute path to owner-only local JSON")
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _stop_on_terminate)
    host = LocalRiskWebHost(args.config)
    try:
        host.start()
        run_local(host.app, origin=host.origin)
    finally:
        host.close()


if __name__ == "__main__":
    main()
