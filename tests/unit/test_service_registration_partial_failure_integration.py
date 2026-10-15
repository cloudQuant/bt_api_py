"""REG02 real-service rollback and optional telemetry degradation.

The exercise uses fixed local Gateway and Monitor adapters with durable SQLite
facts. It has no provider, account credential, exchange, or direct-call fallback.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import multiprocessing
import os
import socket
import sqlite3
import sys
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.service_component import MonitorReadTarget
from bt_api_monitor.service_ingress import MonitorIngressTarget

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_BUSINESS_SERVICE_ID = "reg02-business-service"
_BUSINESS_INSTANCE = "reg02-gateway"
_BUSINESS_SCOPE_REF = "reg02-business-scope-v1"
_BUSINESS_PRINCIPAL = "reg02-business-service-principal"
_BUSINESS_SECURITY_REF = "reg02-business-server-curve-v1"
_TELEMETRY_SERVICE_ID = "reg02-telemetry-service"
_TELEMETRY_INSTANCE = "reg02-telemetry-ingress"
_TELEMETRY_SCOPE_REF = "reg02-telemetry-scope-v1"
_TELEMETRY_PRINCIPAL = "reg02-telemetry-service-principal"
_TELEMETRY_SECURITY_REF = "reg02-telemetry-server-curve-v1"
_MISSING_SERVICE_ID = "reg02-missing-telemetry-service"
_CLIENT_PRINCIPAL = "reg02-strategy-principal"
_STRATEGY_REF = "reg02-strategy"
_ACCOUNT_REF = "reg02-account"
_DOMAIN_REF = "paper:reg02"
_JOURNAL_ACCOUNT = "journal:reg02-account"
_JOURNAL_STRATEGY = "journal:reg02-strategy"
_EVENT_ID = "reg02-existing-position-fact"
_EVENT_TIME = 1_800_000_000.0
_REQUEST_TIMEOUT_MS = 650
_MAX_MESSAGE_BYTES = 65_536
_HOST_LIFETIME_SECONDS = 45.0
_CLIENT_SCOPE = WireScope(
    execution_domain_ref=_DOMAIN_REF,
    account_ref=_ACCOUNT_REF,
    strategy_ref=_STRATEGY_REF,
)
_SERVICE_SCOPE = _CLIENT_SCOPE

_PINNED_MODULES = {
    "sdk": (
        "bt_api_py.service_runtime",
        "bt_api_py.service_host",
        "bt_api_py.service_registry",
        "bt_api_py.service_config",
        "bt_api_py.service_components",
    ),
    "base": ("bt_api_base.service_protocol",),
    "gateway": ("bt_api_gateway.service_component", "bt_api_gateway.event_journal"),
    "monitor": (
        "bt_api_monitor.durable",
        "bt_api_monitor.service_component",
        "bt_api_monitor.service_ingress",
    ),
    "transport": (
        "bt_api_transport_zmq.service_v2",
        "bt_api_transport_zmq.server",
    ),
}


def _expected_source_roots() -> dict[str, Path]:
    names = {
        "sdk": "REG02_SDK_ROOT",
        "base": "REG02_BASE_ROOT",
        "gateway": "REG02_GATEWAY_ROOT",
        "monitor": "REG02_MONITOR_ROOT",
        "transport": "REG02_TRANSPORT_ROOT",
    }
    roots: dict[str, Path] = {}
    for key, env_name in names.items():
        value = os.environ.get(env_name)
        assert value, f"the fixed {key} archive root was not supplied"
        root = Path(value).resolve(strict=True)
        assert root.is_dir(), f"the fixed {key} archive root is not a directory"
        roots[key] = root
    return roots


def _source_origin_snapshot() -> dict[str, Any]:
    roots = _expected_source_roots()
    origins: dict[str, str] = {}
    for group, module_names in _PINNED_MODULES.items():
        for module_name in module_names:
            module = sys.modules.get(module_name)
            if module is None:
                module = importlib.import_module(module_name)
            module_file = getattr(module, "__file__", None)
            assert isinstance(module_file, str), f"{module_name} has no source origin"
            origin = Path(module_file).resolve(strict=True)
            try:
                origin.relative_to(roots[group])
            except ValueError as exc:
                raise AssertionError(
                    f"{module_name} did not load from the fixed {group} archive"
                ) from exc
            origins[module_name] = str(origin)

    runtime_file = str(Path(importlib.import_module("bt_api_py.service_runtime").__file__).resolve())
    assert Path(runtime_file).is_relative_to(roots["sdk"])
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_prefix": str(Path(sys.prefix).resolve()),
        "cwd": str(Path.cwd().resolve()),
        "sdk_service_runtime_file": runtime_file,
        "module_origins": origins,
        "pyzmq_origin": str(Path(zmq.__file__).resolve()),
        "pyzmq_version": str(zmq.__version__),
        "test_file": str(Path(__file__).resolve()),
        "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_roots": {key: str(value) for key, value in roots.items()},
    }


def _available_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"tcp://127.0.0.1:{port}"


def _component(instance_id: str, port: str, options: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {"instance_id": instance_id, "bind_ports": [port], "options": options},
    }


def _communication(endpoint: str, *, server: bool, security_ref: str) -> dict[str, Any]:
    profile = "listener-v1" if server else "client-v1"
    options: dict[str, int] = {
        "high_water_mark": 32,
        "max_message_bytes": _MAX_MESSAGE_BYTES,
    }
    if not server:
        options["request_timeout_ms"] = _REQUEST_TIMEOUT_MS
    return {
        "config_schema_version": 1,
        "profiles": {
            profile: {
                "backend": "zmq",
                "config_schema_version": 1,
                "endpoints": {"rpc_bind" if server else "rpc_connect": endpoint},
                "security_profile_ref": security_ref,
                "options": options,
            }
        },
    }


def _service_config(
    *, service_id: str, security_ref: str, endpoint: str, service_kind: str
) -> dict[str, Any]:
    if service_kind == "business":
        component = _component(
            _BUSINESS_INSTANCE,
            "service.account_read",
            {"role": "account_session_worker", "account_session_ref": "reg02-session-v1"},
        )
        component_name = "gateway"
    elif service_kind == "telemetry":
        component = _component(
            _TELEMETRY_INSTANCE,
            "service.telemetry_ingress",
            {
                "role": "durable_ingress",
                "max_events": 100,
                "max_bytes": 131_072,
                "max_event_bytes": 8_192,
            },
        )
        component_name = "monitor"
    else:
        raise AssertionError("the test selects only fixed service components")
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service_id,
        "scope_ref": f"{service_id}-root-v1",
        "transport_ref": "listener-v1",
        "registration_policy_ref": f"{service_id}-registration-v1",
        "service_requirements_ref": f"{service_id}-requirements-v1",
        "components": {component_name: component},
        "communication": _communication(endpoint, server=True, security_ref=security_ref),
    }


def _resource_grant() -> ResourceGrant:
    return ResourceGrant(
        _BUSINESS_INSTANCE,
        "service.account_read",
        _BUSINESS_SCOPE_REF,
        _SERVICE_SCOPE,
        frozenset({"account.read_after"}),
    )


def _host_process(
    control,
    *,
    raw_config: dict[str, Any],
    registry_path: str,
    database_path: str,
    service_kind: str,
    server_public: bytes,
    server_secret: bytes,
    client_public: bytes,
    normal_close_deadline: float,
) -> None:
    """Spawn target: each child owns one real ServiceHost and CURVE listener."""

    host = None
    try:
        config = parse_service_config(raw_config)
        client_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, _STRATEGY_REF)
        if service_kind == "business":
            service_id = _BUSINESS_SERVICE_ID
            service_principal = _BUSINESS_PRINCIPAL
            security_ref = _BUSINESS_SECURITY_REF
            resource = GatewayServiceAccountScope(
                account_ref=_ACCOUNT_REF,
                execution_domain_ref=_DOMAIN_REF,
                journal_account_scope=_JOURNAL_ACCOUNT,
                strategy_scope_map={_STRATEGY_REF: _JOURNAL_STRATEGY},
            )
            principal = GatewayPrincipal(
                principal_id=_CLIENT_PRINCIPAL,
                account_scopes=frozenset({_JOURNAL_ACCOUNT}),
                strategy_scopes=frozenset({_JOURNAL_STRATEGY}),
                allowed_kinds=frozenset({GatewayCommandKind.READ}),
            )
            grants = {
                _CLIENT_PRINCIPAL: (PrincipalGrant(client_identity, (_resource_grant(),)),)
            }

            def resources(component, _instance_id, _options, _scope):
                if component != "gateway":
                    raise AssertionError("unexpected fixed business component")
                journal = GatewayEventJournal(database_path, clock=lambda: _EVENT_TIME)
                return ComponentResources.borrowed(
                    {
                        "journal": journal,
                        "accounts": {_ACCOUNT_REF: resource},
                        "principals": {_CLIENT_PRINCIPAL: principal},
                    },
                    ready=lambda: True,
                )

            scope = _SERVICE_SCOPE
        elif service_kind == "telemetry":
            service_id = _TELEMETRY_SERVICE_ID
            service_principal = _TELEMETRY_PRINCIPAL
            security_ref = _TELEMETRY_SECURITY_REF
            grants = {
                # This permits discovery of the actual service identity, but
                # grants no resource registration. Register must be rejected.
                _CLIENT_PRINCIPAL: (PrincipalGrant(client_identity, ()),)
            }
            target = MonitorIngressTarget("outbox:reg02-telemetry", ("test.telemetry",))

            def resources(component, _instance_id, _options, _scope):
                if component != "monitor":
                    raise AssertionError("unexpected fixed telemetry component")
                outbox = DurableOutbox(database_path)
                return ComponentResources.borrowed(
                    {"outbox": outbox, "targets": {_SERVICE_SCOPE: target}},
                    ready=lambda: True,
                )

            scope = _SERVICE_SCOPE
        else:
            raise AssertionError("the test selects only fixed ServiceHost roles")

        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=WireIdentity(WireRole.SERVICE, service_principal, service_id),
                scope=scope,
                scope_ref=config.scope_ref,
                scope_version="reg02-service-scope-v1",
                security_ref=security_ref,
                security_version="reg02-curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="reg02-policy-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="reg02-requirements-v1",
                curve_public_key=server_public,
                curve_secret_key=server_secret,
                principals={client_public: _CLIENT_PRINCIPAL},
                grants=grants,
            ),
            registry_path,
            resources,
        )
        host.start()
        origin_snapshot = _source_origin_snapshot()
        control.send(
            ("ready", host.endpoint, origin_snapshot, os.getpid(), normal_close_deadline)
        )
        while True:
            if time.monotonic() >= normal_close_deadline:
                host.close()
                control.send(("closed", host.state, tuple(host.summary["cleanup_history"]), "deadline"))
                return
            host.serve_once(timeout_ms=25)
            if not control.poll():
                continue
            command = control.recv()
            if command == ("close",):
                host.close()
                control.send(("closed", host.state, tuple(host.summary["cleanup_history"]), "request"))
                return
            raise AssertionError("unknown local ServiceHost control command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _start_host(
    context,
    *,
    label: str,
    hosts: list[tuple[str, Any, Any, float]],
    raw_config,
    registry_path,
    database_path,
    service_kind,
    keys,
):
    parent, child = context.Pipe(duplex=True)
    normal_close_deadline = time.monotonic() + _HOST_LIFETIME_SECONDS
    process = context.Process(
        target=_host_process,
        kwargs={
            "control": child,
            "raw_config": raw_config,
            "registry_path": str(registry_path),
            "database_path": str(database_path),
            "service_kind": service_kind,
            "server_public": keys[f"{service_kind}_server_public"],
            "server_secret": keys[f"{service_kind}_server_secret"],
            "client_public": keys["client_public"],
            "normal_close_deadline": normal_close_deadline,
        },
    )
    try:
        process.start()
    except BaseException:
        child.close()
        parent.close()
        process.close()
        raise
    child.close()
    # Register ownership before waiting for readiness so the outer finally can
    # always issue normal close/reap handling if startup times out or errors.
    hosts.append((label, process, parent, normal_close_deadline))
    if not parent.poll(20):
        raise AssertionError(f"spawned {service_kind} ServiceHost did not report startup")
    startup = parent.recv()
    assert startup[0] == "ready", startup
    return process, parent, startup


def _client_config(
    *,
    business_endpoint: str,
    telemetry_endpoint: str | None = None,
    telemetry_required: bool = True,
    telemetry_service_id: str = _TELEMETRY_SERVICE_ID,
):
    bindings_by_alias: dict[str, list[dict[str, Any]]] = {
        "gateway": [
            {
                "mount": "store.account",
                "service_id": _BUSINESS_SERVICE_ID,
                "instance_id": _BUSINESS_INSTANCE,
                "scope_ref": _BUSINESS_SCOPE_REF,
                "account_ref": _ACCOUNT_REF,
                "transport_ref": "business-client-v1",
            }
        ]
    }
    profiles: dict[str, dict[str, Any]] = {
        "business-client-v1": {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": business_endpoint},
            "security_profile_ref": "reg02-business-client-curve-v1",
            "options": {
                "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                "high_water_mark": 32,
                "max_message_bytes": _MAX_MESSAGE_BYTES,
            },
        }
    }
    if telemetry_endpoint is not None:
        bindings_by_alias["monitor"] = [
            {
                "mount": "runtime.telemetry",
                "service_id": telemetry_service_id,
                "instance_id": _TELEMETRY_INSTANCE,
                "scope_ref": _TELEMETRY_SCOPE_REF,
                "transport_ref": "telemetry-client-v1",
                "required": telemetry_required,
            }
        ]
        profiles["telemetry-client-v1"] = {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": telemetry_endpoint},
            "security_profile_ref": "reg02-telemetry-client-curve-v1",
            "options": {
                "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                "high_water_mark": 32,
                "max_message_bytes": _MAX_MESSAGE_BYTES,
            },
        }
    raw = {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _STRATEGY_REF},
        "clients": {
            alias: {
                "config_schema_version": 1,
                "settings": {
                    "bindings": bindings,
                    "options": {"request_timeout_ms": _REQUEST_TIMEOUT_MS},
                },
            }
            for alias, bindings in bindings_by_alias.items()
        },
        "communication": {"config_schema_version": 1, "profiles": profiles},
    }
    return parse_client_config(raw)


def _runtime(
    *,
    business_endpoint: str,
    telemetry_endpoint: str | None,
    telemetry_required: bool,
    telemetry_service_id: str,
    keys: dict[str, bytes],
    run_id: str,
) -> ServiceClientRuntime:
    client_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, _STRATEGY_REF)
    peers = {
        _BUSINESS_SERVICE_ID: WireIdentity(
            WireRole.SERVICE, _BUSINESS_PRINCIPAL, _BUSINESS_SERVICE_ID
        ),
        _TELEMETRY_SERVICE_ID: WireIdentity(
            WireRole.SERVICE, _TELEMETRY_PRINCIPAL, _TELEMETRY_SERVICE_ID
        ),
        _MISSING_SERVICE_ID: WireIdentity(
            WireRole.SERVICE, "reg02-missing-service-principal", _MISSING_SERVICE_ID
        ),
    }
    security_profiles = {
        "reg02-business-client-curve-v1": ClientCurveKeys(
            keys["business_server_public"],
            keys["client_public"],
            keys["client_secret"],
        ),
        "reg02-telemetry-client-curve-v1": ClientCurveKeys(
            keys["telemetry_server_public"],
            keys["client_public"],
            keys["client_secret"],
        ),
    }
    return ServiceClientRuntime(
        _client_config(
            business_endpoint=business_endpoint,
            telemetry_endpoint=telemetry_endpoint,
            telemetry_required=telemetry_required,
            telemetry_service_id=telemetry_service_id,
        ),
        identity=client_identity,
        scope_resolver=lambda _scope_ref: _CLIENT_SCOPE,
        peer_resolver=lambda service_id: peers[service_id],
        transport_factory=ZmqTransportFactory(security_profiles),
        requested_operations={
            "store.account": frozenset({"account.read_after"}),
            "runtime.telemetry": frozenset({"events.append"}),
        },
        client_session_id=f"reg02-session-{run_id}",
        run_id=run_id,
    )


def _registry_snapshot(path: Path, *, instance_id: str) -> dict[str, Any]:
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=3) as connection:
        connection.execute("PRAGMA query_only = ON")
        now = time.time()
        active = tuple(
            row[0]
            for row in connection.execute(
                "SELECT registration_id FROM sessions WHERE instance_id=? AND expires_at>? "
                "ORDER BY registration_id",
                (instance_id, now),
            )
        )
        keys = tuple(
            (row[0], row[1])
            for row in connection.execute(
                "SELECT registration_key, registration_id FROM registration_keys "
                "ORDER BY registration_key"
            )
        )
    return {"active_ids": active, "registration_keys": keys}


def _sqlite_rows(path: Path) -> dict[str, tuple[tuple[object, ...], ...]]:
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=3) as connection:
        connection.execute("PRAGMA query_only = ON")
        names = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        result: dict[str, tuple[tuple[object, ...], ...]] = {}
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = connection.execute(f"SELECT * FROM {quoted}").fetchall()  # noqa: S608
            result[name] = tuple(sorted((tuple(row) for row in rows), key=repr))
    return result


def _business_page(runtime: ServiceClientRuntime) -> dict[str, Any]:
    return dict(runtime.call("store.account", "account.read_after", {"cursor": 0, "limit": 10}))


def _assert_marker(page: dict[str, Any]) -> None:
    events = page["events"]
    assert len(events) == 1
    assert events[0]["event_id"] == _EVENT_ID
    assert events[0]["payload"] == {"symbol": "REG02-SYNTH", "quantity": "7"}


def _close_host(process, control, *, label: str, normal_close_deadline: float) -> dict[str, Any]:
    requested_close = False
    close_message = None
    failures: list[str] = []
    forced_cleanup = False
    if process.is_alive():
        try:
            control.send(("close",))
            requested_close = True
        except (BrokenPipeError, EOFError, OSError) as exc:
            failures.append(f"close_request:{type(exc).__name__}")

    # Give the child a bounded opportunity to acknowledge ServiceHost.close
    # and exit normally before the failure-only process reaper is considered.
    close_deadline = time.monotonic() + 15.0
    while process.is_alive() and time.monotonic() < close_deadline:
        try:
            if control.poll(0.05):
                result = control.recv()
                if result[0] == "ready":
                    # Startup was still completing when cleanup began. The
                    # queued normal close request is consumed by its serve loop.
                    continue
                if result[0] == "closed":
                    close_message = {
                        "state": result[1],
                        "cleanup_history": list(result[2]),
                        "reason": result[3],
                    }
                    break
                failures.append(f"unexpected_child_message:{result[0]}")
        except (BrokenPipeError, EOFError, OSError) as exc:
            failures.append(f"control_pipe:{type(exc).__name__}")
        process.join(0.05)

    process.join(0)
    if close_message is None:
        try:
            if control.poll(0.1):
                result = control.recv()
                if result[0] == "closed":
                    close_message = {
                        "state": result[1],
                        "cleanup_history": list(result[2]),
                        "reason": result[3],
                    }
                elif result[0] != "ready":
                    failures.append(f"unexpected_child_message:{result[0]}")
        except (BrokenPipeError, EOFError, OSError) as exc:
            failures.append(f"control_pipe:{type(exc).__name__}")

    process.join(5)
    if process.is_alive():
        forced_cleanup = True
        try:
            process.terminate()
        except Exception as exc:
            failures.append(f"terminate:{type(exc).__name__}")
        process.join(5)
    if process.is_alive():
        forced_cleanup = True
        try:
            process.kill()
        except Exception as exc:
            failures.append(f"kill:{type(exc).__name__}")
        process.join(5)

    process.join(0)
    alive_after_wait = process.is_alive()
    exit_code = process.exitcode
    if not alive_after_wait:
        try:
            process.close()
        except Exception as exc:
            failures.append(f"process_close:{type(exc).__name__}")
    try:
        control.close()
    except Exception as exc:
        failures.append(f"control_close:{type(exc).__name__}")

    normal_close = (
        not alive_after_wait
        and exit_code == 0
        and not forced_cleanup
        and not failures
        and close_message is not None
        and close_message["state"] == "closed"
        and close_message["cleanup_history"] == []
        and close_message["reason"] in {"request", "deadline"}
    )
    return {
        "normal_close": normal_close,
        "close_message": close_message,
        "close_requested_by_parent": requested_close,
        "process_alive_after_bounded_wait": alive_after_wait,
        "exit_code": exit_code,
        "forced_cleanup": forced_cleanup,
        "cleanup_failures": failures,
        "normal_close_deadline": normal_close_deadline,
    }


def _write_observation(evidence_dir: Path, observation: dict[str, Any]) -> None:
    path = evidence_dir / "observation.json"
    def plain(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [plain(item) for item in value]
        return value

    serialized = json.dumps(plain(observation), indent=2, sort_keys=True) + "\n"
    path.write_text(serialized, encoding="utf-8")


def test_required_multiservice_registration_rolls_back_and_optional_telemetry_degrades(
    tmp_path: Path,
) -> None:
    evidence_value = os.environ.get("REG02_EVIDENCE_DIR")
    assert evidence_value, "REG02 evidence output path was not supplied"
    evidence_dir = Path(evidence_value).resolve(strict=True)
    origins = _source_origin_snapshot()
    observation: dict[str, Any] = {
        "case": "REG02",
        "evidence_level": "LOCAL_REAL_SERVICEHOST_SDK_INTEGRATION",
        "formal_pass_claimed": False,
        "origins": origins,
        "host_children": {},
        "phases": {},
        "cleanup": {},
    }
    journal_path = tmp_path / "business-journal.sqlite3"
    read_registry_path = tmp_path / "business-registry.sqlite3"
    telemetry_registry_path = tmp_path / "telemetry-registry.sqlite3"
    telemetry_outbox_path = tmp_path / "telemetry-outbox.sqlite3"
    _seed_business_fact(journal_path)
    business_rows_before = _sqlite_rows(journal_path)
    business_public, business_secret = zmq.curve_keypair()
    telemetry_public, telemetry_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    keys = {
        "business_server_public": business_public,
        "business_server_secret": business_secret,
        "telemetry_server_public": telemetry_public,
        "telemetry_server_secret": telemetry_secret,
        "client_public": client_public,
        "client_secret": client_secret,
    }
    context = multiprocessing.get_context("spawn")
    hosts: list[tuple[str, Any, Any, float]] = []
    runtimes: list[ServiceClientRuntime] = []
    cleanup_errors: list[str] = []

    try:
        business_host_endpoint = _available_endpoint()
        business_process, business_control, business_startup = _start_host(
            context,
            label="business",
            hosts=hosts,
            raw_config=_service_config(
                service_id=_BUSINESS_SERVICE_ID,
                security_ref=_BUSINESS_SECURITY_REF,
                endpoint=business_host_endpoint,
                service_kind="business",
            ),
            registry_path=read_registry_path,
            database_path=journal_path,
            service_kind="business",
            keys=keys,
        )
        assert business_startup[1] == business_host_endpoint
        observation["host_children"]["business"] = {
            "pid": business_startup[3],
            "endpoint": business_startup[1],
            "origins": business_startup[2],
        }

        telemetry_host_endpoint = _available_endpoint()
        telemetry_process, telemetry_control, telemetry_startup = _start_host(
            context,
            label="telemetry",
            hosts=hosts,
            raw_config=_service_config(
                service_id=_TELEMETRY_SERVICE_ID,
                security_ref=_TELEMETRY_SECURITY_REF,
                endpoint=telemetry_host_endpoint,
                service_kind="telemetry",
            ),
            registry_path=telemetry_registry_path,
            database_path=telemetry_outbox_path,
            service_kind="telemetry",
            keys=keys,
        )
        assert telemetry_startup[1] == telemetry_host_endpoint
        observation["host_children"]["telemetry"] = {
            "pid": telemetry_startup[3],
            "endpoint": telemetry_startup[1],
            "origins": telemetry_startup[2],
        }
        assert business_startup[3] != telemetry_startup[3]

        baseline_runtime = _runtime(
            business_endpoint=business_host_endpoint,
            telemetry_endpoint=None,
            telemetry_required=True,
            telemetry_service_id=_TELEMETRY_SERVICE_ID,
            keys=keys,
            run_id="baseline",
        )
        runtimes.append(baseline_runtime)
        baseline_receipt = baseline_runtime.start()
        assert baseline_receipt.degraded_count == 0
        assert len(baseline_receipt.bindings) == 1
        assert baseline_receipt.bindings[0].mount == "store.account"
        baseline_registration_id = baseline_receipt.bindings[0].registration_id
        baseline_page = _business_page(baseline_runtime)
        _assert_marker(baseline_page)
        observation["phases"]["baseline"] = {
            "state": baseline_runtime.state,
            "registration_id": baseline_registration_id,
            "business_event": baseline_page["events"][0],
        }
        baseline_snapshot = _registry_snapshot(
            read_registry_path, instance_id=_BUSINESS_INSTANCE
        )
        assert baseline_snapshot["active_ids"] == (baseline_registration_id,)
        observation["phases"]["baseline"]["active_registry_ids"] = list(
            baseline_snapshot["active_ids"]
        )

        auth_before = _registry_snapshot(read_registry_path, instance_id=_BUSINESS_INSTANCE)
        rejected_runtime = _runtime(
            business_endpoint=business_host_endpoint,
            telemetry_endpoint=telemetry_host_endpoint,
            telemetry_required=True,
            telemetry_service_id=_TELEMETRY_SERVICE_ID,
            keys=keys,
            run_id="required-auth-reject",
        )
        runtimes.append(rejected_runtime)
        with pytest.raises(ServiceRuntimeError) as auth_error:
            rejected_runtime.start()
        assert auth_error.value.code == "SCOPE_DENIED"
        assert rejected_runtime.state == "failed"
        assert rejected_runtime.bindings == ()
        assert rejected_runtime.cleanup_failures == 0
        assert rejected_runtime.unregister_uncertainties == 0
        assert rejected_runtime.unknown_registration_count == 0
        with pytest.raises(ServiceRuntimeError) as auth_route_error:
            rejected_runtime.call("store.account", "account.read_after", {"cursor": 0, "limit": 10})
        assert auth_route_error.value.code == "SESSION_NOT_REGISTERED"
        auth_after = _registry_snapshot(read_registry_path, instance_id=_BUSINESS_INSTANCE)
        auth_attempt_keys = set(auth_after["registration_keys"]) - set(
            auth_before["registration_keys"]
        )
        assert len(auth_attempt_keys) == 1
        assert auth_after["active_ids"] == auth_before["active_ids"] == (
            baseline_registration_id,
        )
        assert {row[1] for row in auth_attempt_keys}.isdisjoint(auth_after["active_ids"])
        denied_service_state = _registry_snapshot(
            telemetry_registry_path, instance_id=_TELEMETRY_INSTANCE
        )
        assert denied_service_state["active_ids"] == ()
        assert denied_service_state["registration_keys"] == ()
        _assert_marker(_business_page(baseline_runtime))
        observation["phases"]["required_authorization_rejection"] = {
            "service_host": _TELEMETRY_SERVICE_ID,
            "service_reply_code": auth_error.value.code,
            "runtime_state": rejected_runtime.state,
            "cleanup_failures": rejected_runtime.cleanup_failures,
            "unregister_uncertainties": rejected_runtime.unregister_uncertainties,
            "unknown_registrations": rejected_runtime.unknown_registration_count,
            "business_registration_before": list(auth_before["active_ids"]),
            "business_registration_after": list(auth_after["active_ids"]),
            "transient_registration_key_rows": [list(row) for row in sorted(auth_attempt_keys)],
            "rejected_service_active_ids": list(denied_service_state["active_ids"]),
            "rejected_service_registration_keys": list(denied_service_state["registration_keys"]),
            "failed_runtime_call_code": auth_route_error.value.code,
        }

        missing_endpoint = _available_endpoint()
        missing_before = _registry_snapshot(read_registry_path, instance_id=_BUSINESS_INSTANCE)
        missing_runtime = _runtime(
            business_endpoint=business_host_endpoint,
            telemetry_endpoint=missing_endpoint,
            telemetry_required=True,
            telemetry_service_id=_MISSING_SERVICE_ID,
            keys=keys,
            run_id="required-endpoint-missing",
        )
        runtimes.append(missing_runtime)
        with pytest.raises(ServiceRuntimeError) as missing_error:
            missing_runtime.start()
        assert missing_error.value.code in {"REQUEST_NOT_SENT", "REQUIRED_REGISTRATION_FAILED"}
        assert missing_runtime.state == "failed"
        assert missing_runtime.bindings == ()
        assert missing_runtime.cleanup_failures == 0
        assert missing_runtime.unregister_uncertainties == 0
        assert missing_runtime.unknown_registration_count == 0
        with pytest.raises(ServiceRuntimeError) as missing_route_error:
            missing_runtime.call("store.account", "account.read_after", {"cursor": 0, "limit": 10})
        assert missing_route_error.value.code == "SESSION_NOT_REGISTERED"
        missing_after = _registry_snapshot(read_registry_path, instance_id=_BUSINESS_INSTANCE)
        missing_attempt_keys = set(missing_after["registration_keys"]) - set(
            missing_before["registration_keys"]
        )
        assert len(missing_attempt_keys) == 1
        assert missing_after["active_ids"] == missing_before["active_ids"] == (
            baseline_registration_id,
        )
        assert {row[1] for row in missing_attempt_keys}.isdisjoint(missing_after["active_ids"])
        _assert_marker(_business_page(baseline_runtime))
        observation["phases"]["required_endpoint_missing"] = {
            "unbound_local_endpoint": missing_endpoint,
            "service_reply_or_transport_code": missing_error.value.code,
            "runtime_state": missing_runtime.state,
            "cleanup_failures": missing_runtime.cleanup_failures,
            "unregister_uncertainties": missing_runtime.unregister_uncertainties,
            "unknown_registrations": missing_runtime.unknown_registration_count,
            "business_registration_before": list(missing_before["active_ids"]),
            "business_registration_after": list(missing_after["active_ids"]),
            "transient_registration_key_rows": [
                list(row) for row in sorted(missing_attempt_keys)
            ],
            "failed_runtime_call_code": missing_route_error.value.code,
        }

        optional_endpoint = _available_endpoint()
        optional_before = _registry_snapshot(read_registry_path, instance_id=_BUSINESS_INSTANCE)
        optional_runtime = _runtime(
            business_endpoint=business_host_endpoint,
            telemetry_endpoint=optional_endpoint,
            telemetry_required=False,
            telemetry_service_id=_MISSING_SERVICE_ID,
            keys=keys,
            run_id="optional-telemetry-missing",
        )
        runtimes.append(optional_runtime)
        optional_receipt = optional_runtime.start()
        assert optional_receipt.degraded_count == 1
        assert len(optional_receipt.degradation_codes) == 1
        assert optional_receipt.degradation_codes[0] in {
            "REQUEST_NOT_SENT",
            "SERVICE_UNAVAILABLE",
        }
        assert tuple(binding.mount for binding in optional_receipt.bindings) == ("store.account",)
        optional_page = _business_page(optional_runtime)
        _assert_marker(optional_page)
        with pytest.raises(ServiceRuntimeError) as optional_route_error:
            optional_runtime.call("runtime.telemetry", "events.append", {})
        assert optional_route_error.value.code == "AMBIGUOUS_OR_MISSING_ROUTE"
        optional_after = _registry_snapshot(read_registry_path, instance_id=_BUSINESS_INSTANCE)
        optional_attempt_keys = set(optional_after["registration_keys"]) - set(
            optional_before["registration_keys"]
        )
        optional_registration_id = optional_receipt.bindings[0].registration_id
        assert optional_after["active_ids"] == tuple(
            sorted((baseline_registration_id, optional_registration_id))
        )
        assert len(optional_attempt_keys) == 1
        assert optional_registration_id in {row[1] for row in optional_attempt_keys}
        optional_runtime.close()
        optional_closed = _registry_snapshot(read_registry_path, instance_id=_BUSINESS_INSTANCE)
        assert optional_closed["active_ids"] == (baseline_registration_id,)
        _assert_marker(_business_page(baseline_runtime))
        observation["phases"]["optional_telemetry_missing"] = {
            "unbound_local_endpoint": optional_endpoint,
            "runtime_state": optional_runtime.state,
            "degraded_count": optional_receipt.degraded_count,
            "degradation_codes": list(optional_receipt.degradation_codes),
            "registered_mounts": [binding.mount for binding in optional_receipt.bindings],
            "business_event": optional_page["events"][0],
            "unavailable_mount_call_code": optional_route_error.value.code,
            "active_registration_with_optional_runtime": list(optional_after["active_ids"]),
            "active_registration_after_optional_close": list(optional_closed["active_ids"]),
            "transient_business_registration_id": optional_registration_id,
        }
    finally:
        for runtime in reversed(runtimes):
            try:
                if runtime.state != "closed":
                    runtime.close()
                observation["cleanup"].setdefault("runtimes", []).append(
                    {
                        "state": runtime.state,
                        "cleanup_failures": runtime.cleanup_failures,
                        "unregister_uncertainties": runtime.unregister_uncertainties,
                    }
                )
            except BaseException as exc:
                cleanup_errors.append(f"runtime:{type(exc).__name__}")
        if read_registry_path.exists():
            try:
                final_registry = _registry_snapshot(
                    read_registry_path, instance_id=_BUSINESS_INSTANCE
                )
                observation["cleanup"]["business_registry_after_runtime_close"] = {
                    "active_ids": list(final_registry["active_ids"]),
                    "registration_key_count": len(final_registry["registration_keys"]),
                }
                if final_registry["active_ids"]:
                    cleanup_errors.append("business_registry_has_active_sessions")
            except BaseException as exc:
                cleanup_errors.append(f"business_registry:{type(exc).__name__}")
        for label, process, control, normal_close_deadline in reversed(hosts):
            try:
                close_result = _close_host(
                    process,
                    control,
                    label=label,
                    normal_close_deadline=normal_close_deadline,
                )
                observation["cleanup"][label] = close_result
                if close_result["forced_cleanup"]:
                    cleanup_errors.append(f"host:{label}:forced_cleanup")
                if close_result["cleanup_failures"]:
                    cleanup_errors.append(f"host:{label}:cleanup_failures")
                if not close_result["normal_close"]:
                    cleanup_errors.append(f"host:{label}:normal_close_unconfirmed")
            except BaseException as exc:
                cleanup_errors.append(f"host:{label}:{type(exc).__name__}")
                with suppress(Exception):
                    control.close()
        observation["cleanup"]["errors"] = cleanup_errors
        observation["cleanup"]["forced_cleanup_hosts"] = [
            label
            for label in ("business", "telemetry")
            if observation["cleanup"].get(label, {}).get("forced_cleanup")
        ]
        observation["business_fact_unchanged"] = (
            _sqlite_rows(journal_path) == business_rows_before if journal_path.exists() else False
        )
        observation["formal_pass_claimed"] = False
        _write_observation(evidence_dir, observation)
        assert not cleanup_errors, cleanup_errors
        assert not observation["cleanup"]["forced_cleanup_hosts"]
        assert observation["business_fact_unchanged"] is True


def _seed_business_fact(journal_path: Path) -> None:
    journal = GatewayEventJournal(journal_path, clock=lambda: _EVENT_TIME)
    journal.append(
        _JOURNAL_ACCOUNT,
        _JOURNAL_STRATEGY,
        _EVENT_ID,
        "position_update",
        {"symbol": "REG02-SYNTH", "quantity": "7"},
        observed_at=_EVENT_TIME,
    )
