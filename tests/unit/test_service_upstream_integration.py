"""Two spawned ServiceHosts with a real gateway-to-monitor projection."""

from __future__ import annotations

import multiprocessing
import socket
import time
from contextlib import suppress
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_monitor.durable import DurableOutbox, OutboxEvent
from bt_api_monitor.service_component import MonitorReadTarget

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import (
    ServiceHost,
    ServiceHostError,
    TrustedServiceAuthority,
    TrustedServiceClients,
)
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_UPSTREAM_ID = "synthetic-gateway-service"
_DOWNSTREAM_ID = "synthetic-monitor-service"
_APP_ID = "synthetic-monitor-app"
_UPSTREAM_PRINCIPAL = "synthetic-downstream-principal"
_DOWNSTREAM_PRINCIPAL = "synthetic-monitor-principal"
_APP_PRINCIPAL = "synthetic-app-principal"
_ACCOUNT_REF = "synthetic-account"
_STRATEGY_REF = "synthetic-strategy"
_DOMAIN_REF = "paper:synthetic"
_SOURCE_SCOPE = WireScope(
    execution_domain_ref=_DOMAIN_REF,
    account_ref=_ACCOUNT_REF,
    strategy_ref=_STRATEGY_REF,
)
_LOCAL_SCOPE = _SOURCE_SCOPE
_JOURNAL_ACCOUNT = "journal:synthetic-account"
_JOURNAL_STRATEGY = "journal:synthetic-strategy"
_OUTBOX_SCOPE = "outbox:synthetic-strategy"
_OUTBOX_TYPE = "account.projected"
_CURVE_BYTES = 65_536
_TIMEOUT_MS = 1_500
_HEARTBEAT_SECONDS = 5
_SERVER_OPTIONS = {"high_water_mark": 32, "max_message_bytes": _CURVE_BYTES}


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _component(name: str, port: str, options: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {"instance_id": name, "bind_ports": [port], "options": options},
    }


def _listener(endpoint: str, security_ref: str) -> dict[str, Any]:
    return {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": {"rpc_bind": endpoint},
        "security_profile_ref": security_ref,
        "options": _SERVER_OPTIONS,
    }


def _outbound(endpoint: str) -> dict[str, Any]:
    return {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": {"rpc_connect": endpoint},
        "security_profile_ref": "gateway-client-security-v1",
        "options": {
            "request_timeout_ms": _TIMEOUT_MS,
            "high_water_mark": 32,
            "max_message_bytes": _CURVE_BYTES,
        },
    }


def _service_config(
    service_id: str,
    endpoint: str,
    security_ref: str,
    component_name: str,
    port: str,
    options: dict[str, Any],
    *,
    upstream_endpoint: str | None = None,
) -> dict[str, Any]:
    profiles = {"listener-v1": _listener(endpoint, security_ref)}
    raw: dict[str, Any] = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service_id,
        "scope_ref": f"{service_id}-root-v1",
        "transport_ref": "listener-v1",
        "registration_policy_ref": f"{service_id}-registration-v1",
        "service_requirements_ref": f"{service_id}-requirements-v1",
        "components": {component_name: _component(f"{component_name}-read", port, options)},
        "communication": {"config_schema_version": 1, "profiles": profiles},
    }
    if upstream_endpoint is not None:
        raw["clients"] = {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "upstream.account",
                            "account_ref": _ACCOUNT_REF,
                            "service_id": _UPSTREAM_ID,
                            "instance_id": "gateway-read",
                            "scope_ref": "upstream-account-read-v1",
                            "transport_ref": "gateway-connect-v1",
                        }
                    ],
                    "options": {
                        "request_timeout_ms": _TIMEOUT_MS,
                        "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                    },
                },
            }
        }
        profiles["gateway-connect-v1"] = _outbound(upstream_endpoint)
    return raw


def _authority(
    config,
    *,
    principal_identity: WireIdentity,
    principal_public: bytes,
    principal_ref: str,
    component_instance: str,
    port: str,
    grant_ref: str,
    operations: frozenset[str],
    scope: WireScope,
    server_public: bytes,
    server_secret: bytes,
    security_ref: str,
) -> TrustedServiceAuthority:
    return TrustedServiceAuthority(
        identity=WireIdentity(
            WireRole.SERVICE, f"{config.service_id}-principal", config.service_id
        ),
        scope=scope,
        scope_ref=config.scope_ref,
        scope_version="service-scope-v1",
        security_ref=security_ref,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={principal_public: principal_ref},
        grants={
            principal_ref: (
                PrincipalGrant(
                    principal_identity,
                    (
                        ResourceGrant(
                            component_instance,
                            port,
                            grant_ref,
                            scope,
                            operations,
                        ),
                    ),
                ),
            )
        },
    )


def _source_event(path: str, sequence: int) -> None:
    journal = GatewayEventJournal(path)
    journal.append(
        _JOURNAL_ACCOUNT,
        _JOURNAL_STRATEGY,
        f"source-event-{sequence}",
        "position_update",
        {"symbol": "SYNTH-ASSET", "value": sequence},
        observed_at=1_800_000_000.0 + sequence,
    )


def _gateway_host_process(control, raw_config, registry_path, journal_path, keys) -> None:
    config = parse_service_config(raw_config)
    upstream_identity = WireIdentity(
        WireRole.SERVICE, f"{_DOWNSTREAM_ID}-principal", _DOWNSTREAM_ID
    )
    scope = _SOURCE_SCOPE

    def resources(_component, _instance, _options, _scope):
        return ComponentResources.borrowed(
            {
                "journal": GatewayEventJournal(journal_path),
                "accounts": {
                    _ACCOUNT_REF: GatewayServiceAccountScope(
                        account_ref=_ACCOUNT_REF,
                        execution_domain_ref=_DOMAIN_REF,
                        journal_account_scope=_JOURNAL_ACCOUNT,
                        strategy_scope_map={_STRATEGY_REF: _JOURNAL_STRATEGY},
                    )
                },
                "principals": {
                    _UPSTREAM_PRINCIPAL: GatewayPrincipal(
                        principal_id=_UPSTREAM_PRINCIPAL,
                        account_scopes=frozenset({_JOURNAL_ACCOUNT}),
                        strategy_scopes=frozenset({_JOURNAL_STRATEGY}),
                        allowed_kinds=frozenset({GatewayCommandKind.READ}),
                    )
                },
            },
            ready=lambda: True,
        )

    host = ServiceHost(
        config,
        _authority(
            config,
            principal_identity=upstream_identity,
            principal_public=keys["client_public"],
            principal_ref=_UPSTREAM_PRINCIPAL,
            component_instance="gateway-read",
            port="service.account_read",
            grant_ref="upstream-account-read-v1",
            operations=frozenset({"account.read_after"}),
            scope=scope,
            server_public=keys["server_public"],
            server_secret=keys["server_secret"],
            security_ref="gateway-server-security-v1",
        ),
        registry_path,
        resources,
    )
    try:
        host.start()
        control.send(("ready", host.endpoint))
        while True:
            if control.poll():
                command = control.recv()
                if command == "close":
                    host.close()
                    control.send(("closed", host.state))
                    return
            host.serve_once(30)
    except BaseException as exc:
        if host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _project_one_page(host: ServiceHost, outbox: DurableOutbox, cursor: int) -> tuple[int, int]:
    upstream = host.upstream
    if upstream is None or not upstream.healthy:
        raise ServiceHostError("UPSTREAM_NOT_HEALTHY")
    page = upstream.call("upstream.account", "account.read_after", {"cursor": cursor, "limit": 25})
    count = 0
    for event in page["events"]:
        source_id = str(event["event_id"])
        outbox.append(
            OutboxEvent(
                event_id=f"projection:{source_id}",
                scope=_OUTBOX_SCOPE,
                event_type=_OUTBOX_TYPE,
                data={
                    "source_event_id": source_id,
                    "source_sequence": event["sequence"],
                    "payload": dict(event["payload"]),
                },
                occurred_at=float(event["observed_at"]),
            )
        )
        count += 1
    return count, int(page["next_cursor"])


def _monitor_host_process(
    control,
    upstream_raw,
    raw_config,
    upstream_endpoint,
    registry_path,
    outbox_path,
    keys,
) -> None:
    upstream_config = parse_service_config(upstream_raw)
    config = parse_service_config(raw_config)
    scope = _LOCAL_SCOPE
    writer = DurableOutbox(outbox_path)
    reader = DurableOutbox(outbox_path, read_only=True)

    def resources(component, _instance, _options, _scope):
        if component != "monitor":
            raise AssertionError("only the fixed monitor adapter is selected")
        return ComponentResources.borrowed(
            {
                "outbox": reader,
                "targets": {scope: MonitorReadTarget(_OUTBOX_SCOPE, (_OUTBOX_TYPE,))},
            },
            ready=lambda: reader.is_read_only,
        )

    app_identity = WireIdentity(WireRole.APPLICATION_CLIENT, _APP_PRINCIPAL, _APP_ID)
    app_grant = "monitor-app-read-v1"
    host = None
    try:
        host = ServiceHost(
            config,
            _authority(
                config,
                principal_identity=app_identity,
                principal_public=keys["app_public"],
                principal_ref=_APP_PRINCIPAL,
                component_instance="monitor-read",
                port="service.monitor_query",
                grant_ref=app_grant,
                operations=frozenset({"monitor.read_after"}),
                scope=scope,
                server_public=keys["monitor_public"],
                server_secret=keys["monitor_secret"],
                security_ref="monitor-server-security-v1",
            ),
            registry_path,
            resources,
            service_directory={
                _UPSTREAM_ID: upstream_config,
                _DOWNSTREAM_ID: config,
            },
            trusted_clients=TrustedServiceClients(
                scope_resolver=lambda _ref: _SOURCE_SCOPE,
                peer_resolver=lambda _service: WireIdentity(
                    WireRole.SERVICE, f"{_UPSTREAM_ID}-principal", _UPSTREAM_ID
                ),
                transport_factory=ZmqTransportFactory(
                    {
                        "gateway-client-security-v1": ClientCurveKeys(
                            keys["gateway_public"],
                            keys["client_public"],
                            keys["client_secret"],
                        )
                    }
                ),
                requested_operations={"upstream.account": frozenset({"account.read_after"})},
            ),
        )
        host.start()
        assert host.upstream is not None and host.upstream.healthy
        cursor = 0
        projected, cursor = _project_one_page(host, writer, cursor)
        deadline = host.upstream.bindings[0].lease_deadline
        renewals = 0
        paused = False
        failure = None
        next_projection = time.monotonic() + 0.2
        control.send(("ready", host.endpoint, projected, deadline))

        while True:
            host.serve_once(50)
            current_deadline = host.upstream.bindings[0].lease_deadline
            if current_deadline > deadline:
                deadline = current_deadline
                renewals += 1
            now = time.monotonic()
            if not paused and now >= next_projection and host.upstream.healthy:
                try:
                    _count, cursor = _project_one_page(host, writer, cursor)
                except Exception as exc:
                    # Pause the producer after uncertainty; it issues no
                    # business write until explicit upstream recovery.
                    paused = True
                    failure = getattr(exc, "code", "UPSTREAM_READ_FAILED")
                else:
                    failure = None
                next_projection = now + 0.25

            if not control.poll():
                continue
            command = control.recv()
            if command == "status":
                upstream = host.upstream
                control.send(
                    (
                        "status",
                        host.healthy,
                        bool(upstream and upstream.healthy),
                        renewals,
                        paused,
                        failure,
                    )
                )
            elif command == "recover":
                try:
                    summary = host.recover_upstreams()
                    paused = False
                    failure = None
                    next_projection = time.monotonic() + 0.2
                    control.send(("recovered", summary.degraded_count, host.healthy))
                except ServiceHostError as exc:
                    control.send(("recover_error", exc.code, host.healthy))
            elif command == "close":
                host.close()
                control.send(("closed", host.state, host.summary["upstream"]))
                return
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _start_process(context, target, args):
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=target, args=(child, *args))
    process.start()
    child.close()
    return process, parent


def _receive(pipe, timeout: float = 15.0):
    assert pipe.poll(timeout), "spawned ServiceHost did not answer"
    return pipe.recv()


def _stop_process(process, pipe) -> None:
    if process.is_alive():
        pipe.send("close")
        closed = _receive(pipe)
        assert closed[0] == "closed", closed
    process.join(15)
    assert process.exitcode == 0
    assert not process.is_alive()
    pipe.close()
    process.close()


def _outbox_events(path: str) -> tuple[Any, ...]:
    reader = DurableOutbox(path, read_only=True)
    rows, _has_more = reader.read_page_types(_OUTBOX_SCOPE, (_OUTBOX_TYPE,), 0, 50)
    return tuple(row.event for row in rows)


def _monitor_client_config(endpoint: str) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": _APP_ID},
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.monitor",
                            "service_id": _DOWNSTREAM_ID,
                            "instance_id": "monitor-read",
                            "scope_ref": "monitor-app-read-v1",
                            "transport_ref": "monitor-client-v1",
                        }
                    ],
                    "options": {
                        "request_timeout_ms": _TIMEOUT_MS,
                        "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "monitor-client-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": "monitor-client-security-v1",
                    "options": {
                        "request_timeout_ms": _TIMEOUT_MS,
                        "high_water_mark": 32,
                        "max_message_bytes": _CURVE_BYTES,
                    },
                }
            },
        },
    }


def _monitor_page(runtime: ServiceClientRuntime):
    return runtime.call(
        "application.monitor",
        "monitor.read_after",
        {"after_sequence": 0, "limit": 50},
    )["events"]


def _maintain_until(runtime: ServiceClientRuntime, deadline: float) -> None:
    while time.monotonic() < deadline:
        delay = runtime.next_maintenance_delay()
        if delay is not None and delay <= 0:
            runtime.maintain()
        time.sleep(0.05)


def test_spawned_gateway_projects_to_monitor_with_renewal_and_explicit_recovery(tmp_path):
    context = multiprocessing.get_context("spawn")
    source_path = str(tmp_path / "gateway.sqlite3")
    outbox_path = str(tmp_path / "monitor.sqlite3")
    upstream_registry = str(tmp_path / "gateway-registry.sqlite3")
    downstream_registry = str(tmp_path / "monitor-registry.sqlite3")
    gateway_endpoint, monitor_endpoint = _endpoint(), _endpoint()
    _source_event(source_path, 1)

    gateway_server_public, gateway_server_secret = zmq.curve_keypair()
    gateway_client_public, gateway_client_secret = zmq.curve_keypair()
    monitor_server_public, monitor_server_secret = zmq.curve_keypair()
    monitor_client_public, monitor_client_secret = zmq.curve_keypair()
    gateway_config = _service_config(
        _UPSTREAM_ID,
        gateway_endpoint,
        "gateway-server-security-v1",
        "gateway",
        "service.account_read",
        {"role": "account_session_worker", "account_session_ref": "synthetic-session-v1"},
    )
    monitor_config = _service_config(
        _DOWNSTREAM_ID,
        monitor_endpoint,
        "monitor-server-security-v1",
        "monitor",
        "service.monitor_query",
        {},
        upstream_endpoint=gateway_endpoint,
    )
    source_keys = {
        "server_public": gateway_server_public,
        "server_secret": gateway_server_secret,
        "client_public": gateway_client_public,
    }
    monitor_keys = {
        "monitor_public": monitor_server_public,
        "monitor_secret": monitor_server_secret,
        "app_public": monitor_client_public,
        "gateway_public": gateway_server_public,
        "client_public": gateway_client_public,
        "client_secret": gateway_client_secret,
    }
    gateway_args = (
        gateway_config,
        upstream_registry,
        source_path,
        source_keys,
    )
    gateway_process, gateway_pipe = _start_process(context, _gateway_host_process, gateway_args)
    monitor_process = monitor_pipe = None
    app_runtime = None
    try:
        ready = _receive(gateway_pipe)
        assert ready[0] == "ready", ready
        monitor_args = (
            gateway_config,
            monitor_config,
            gateway_endpoint,
            downstream_registry,
            outbox_path,
            monitor_keys,
        )
        monitor_process, monitor_pipe = _start_process(context, _monitor_host_process, monitor_args)
        ready = _receive(monitor_pipe)
        assert ready[0] == "ready" and ready[2] == 1, ready
        downstream_ready_at = time.monotonic()

        downstream_identity = WireIdentity(
            WireRole.SERVICE, f"{_DOWNSTREAM_ID}-principal", _DOWNSTREAM_ID
        )
        app_identity = WireIdentity(WireRole.APPLICATION_CLIENT, _APP_PRINCIPAL, _APP_ID)
        app_keys = ClientCurveKeys(
            monitor_server_public, monitor_client_public, monitor_client_secret
        )
        app_runtime = ServiceClientRuntime(
            parse_client_config(_monitor_client_config(monitor_endpoint)),
            identity=app_identity,
            scope_resolver=lambda _ref: _LOCAL_SCOPE,
            peer_resolver=lambda _service: downstream_identity,
            transport_factory=ZmqTransportFactory({"monitor-client-security-v1": app_keys}),
            requested_operations={"application.monitor": frozenset({"monitor.read_after"})},
        )
        receipt = app_runtime.start()
        assert receipt.degraded_count == 0
        assert [event["data"]["source_event_id"] for event in _monitor_page(app_runtime)] == [
            "source-event-1"
        ]

        # Both owner threads renew their real 30-second registry leases. The
        # app runtime is maintained here; ServiceHost maintains its upstream.
        initial_app_deadline = app_runtime.bindings[0].lease_deadline
        _maintain_until(
            app_runtime,
            max(initial_app_deadline + 0.25, downstream_ready_at + 30.25),
        )
        assert app_runtime.healthy
        assert app_runtime.bindings[0].lease_deadline > initial_app_deadline
        source_events = _outbox_events(outbox_path)
        assert len(source_events) == 1
        status = monitor_pipe
        status.send("status")
        upstream_status = _receive(status)
        assert upstream_status[0] == "status" and upstream_status[2] is True, upstream_status
        assert upstream_status[3] >= 1
        assert time.monotonic() - downstream_ready_at > 30

        # A new source row is projected once after the downstream's upstream
        # lease has already crossed its first TTL.
        _source_event(source_path, 2)
        time.sleep(0.5)
        projected = _monitor_page(app_runtime)
        assert [event["data"]["source_event_id"] for event in projected] == [
            "source-event-1",
            "source-event-2",
        ]
        assert [event.event_id for event in _outbox_events(outbox_path)] == [
            "projection:source-event-1",
            "projection:source-event-2",
        ]

        # Stop the upstream normally, then add a row while its listener is
        # unavailable. The failed required lease makes the downstream unready;
        # its producer pauses and does not write/replay during the outage.
        _stop_process(gateway_process, gateway_pipe)
        gateway_process = None
        gateway_pipe = None
        _source_event(source_path, 3)
        unavailable = False
        until = time.monotonic() + 15
        while time.monotonic() < until:
            try:
                _monitor_page(app_runtime)
            except ServiceRuntimeError as exc:
                if exc.code == "POLICY_REQUIREMENT_UNMET":
                    unavailable = True
                    break
                raise
            time.sleep(0.2)
        assert unavailable, "downstream kept serving while required upstream was unavailable"
        monitor_pipe.send("status")
        paused_status = _receive(monitor_pipe)
        assert paused_status[0] == "status" and paused_status[1:3] == (False, False)
        assert paused_status[4] is True
        assert [event.event_id for event in _outbox_events(outbox_path)] == [
            "projection:source-event-1",
            "projection:source-event-2",
        ]

        # Restart the upstream at its original local endpoint and explicitly
        # recover the required client. Recovery registers again but does not
        # itself replay a business query; the producer loop resumes afterward.
        gateway_process, gateway_pipe = _start_process(context, _gateway_host_process, gateway_args)
        ready = _receive(gateway_pipe)
        assert ready[0] == "ready", ready
        monitor_pipe.send("recover")
        recovered = _receive(monitor_pipe)
        assert recovered == ("recovered", 0, True)
        app_runtime.recover()
        assert [event["data"]["source_event_id"] for event in _monitor_page(app_runtime)] == [
            "source-event-1",
            "source-event-2",
        ]
        time.sleep(0.5)
        final_page = _monitor_page(app_runtime)
        assert [event["data"]["source_event_id"] for event in final_page] == [
            "source-event-1",
            "source-event-2",
            "source-event-3",
        ]
        assert len({event["event_id"] for event in final_page}) == 3
        assert [event.event_id for event in _outbox_events(outbox_path)] == [
            "projection:source-event-1",
            "projection:source-event-2",
            "projection:source-event-3",
        ]
        app_runtime.close()
        assert app_runtime.state == "closed"
        assert app_runtime.cleanup_failures == 0
        _stop_process(monitor_process, monitor_pipe)
        monitor_process = None
        monitor_pipe = None
        _stop_process(gateway_process, gateway_pipe)
        gateway_process = None
        gateway_pipe = None
    finally:
        if app_runtime is not None and app_runtime.state != "closed":
            with suppress(Exception):
                app_runtime.close()
        for process, pipe in ((monitor_process, monitor_pipe), (gateway_process, gateway_pipe)):
            if process is not None:
                if process.is_alive():
                    with suppress(Exception):
                        pipe.send("close")
                        _receive(pipe, 5)
                process.join(10)
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                with suppress(Exception):
                    pipe.close()
                if process.exitcode is not None:
                    process.close()
