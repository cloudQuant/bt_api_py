"""Ordinary strategy telemetry over spawned hosts and CURVE RPC."""

from __future__ import annotations

import importlib.abc
import multiprocessing
import socket
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import zmq
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

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
from bt_api_py.service_telemetry import ServiceTelemetryEmitter

_SERVICE_ID = "synthetic-telemetry-monitor"
_SERVICE_PRINCIPAL = "synthetic-telemetry-monitor-principal"
_SERVICE_INSTANCE = "monitor-telemetry"
_SERVICE_SCOPE_REF = "monitor-telemetry-root-v1"
_SERVER_SECURITY_REF = "monitor-telemetry-server-curve-v1"
_STRATEGY_A = "strategy-telemetry-a"
_STRATEGY_B = "strategy-telemetry-b"
_PRINCIPAL_A = "telemetry-strategy-a-principal"
_PRINCIPAL_B = "telemetry-strategy-b-principal"
_APP_ID = "synthetic-telemetry-dashboard"
_APP_PRINCIPAL = "telemetry-dashboard-principal"
_DOMAIN = "paper:telemetry-integration"
_SCOPE_A_REF = "strategy-telemetry-a-v1"
_SCOPE_B_REF = "strategy-telemetry-b-v1"
_APP_SCOPE_A_REF = "telemetry-dashboard-a-v1"
_APP_SCOPE_B_REF = "telemetry-dashboard-b-v1"
_REQUEST_TIMEOUT_MS = 500
_MAX_MESSAGE_BYTES = 65_536
_DOMAIN_PACKAGES = (
    "bt_api_gateway",
    "bt_api_monitor",
    "bt_api_risk",
    "bt_api_execution",
)
_SCOPE_A = WireScope(_DOMAIN, strategy_ref=_STRATEGY_A)
_SCOPE_B = WireScope(_DOMAIN, strategy_ref=_STRATEGY_B)


class _DomainImportBlocker(importlib.abc.MetaPathFinder):
    """Keep the spawned strategy worker limited to SDK/base client code."""

    def find_spec(self, fullname, path=None, target=None):
        del path, target
        if any(fullname == name or fullname.startswith(f"{name}.") for name in _DOMAIN_PACKAGES):
            raise AssertionError("strategy telemetry worker attempted a domain-package import")
        return None


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _component(instance_id: str, ports: tuple[str, ...]) -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": list(ports),
            "options": {
                "role": "telemetry_ingress",
                "max_streams": 16,
                "max_bytes": 65_536,
                "heartbeat_timeout_seconds": 2,
                "retention_seconds": 60,
            },
        },
    }


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": "telemetry-listener-v1",
        "registration_policy_ref": "telemetry-registration-v1",
        "service_requirements_ref": "telemetry-requirements-v1",
        "components": {
            "monitor": _component(
                _SERVICE_INSTANCE,
                ("service.telemetry_ingress", "service.monitor_query"),
            )
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "telemetry-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SERVER_SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _strategy_config(endpoint: str, strategy_ref: str, scope_ref: str, security_ref: str):
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": strategy_ref},
            "clients": {
                "monitor": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "runtime.telemetry",
                                "service_id": _SERVICE_ID,
                                "instance_id": _SERVICE_INSTANCE,
                                "scope_ref": scope_ref,
                                "transport_ref": "telemetry-connect-v1",
                                "required": False,
                            }
                        ],
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "heartbeat_interval_seconds": 5,
                            "telemetry_queue_max_events": 2,
                            "telemetry_queue_max_bytes": 8192,
                        },
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "telemetry-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": security_ref,
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    }
                },
            },
        }
    )


def _application_config(endpoint: str):
    return parse_client_config(
        {
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
                                "service_id": _SERVICE_ID,
                                "instance_id": _SERVICE_INSTANCE,
                                "scope_ref": _APP_SCOPE_A_REF,
                                "transport_ref": "telemetry-query-v1",
                            },
                            {
                                "mount": "application.monitor",
                                "service_id": _SERVICE_ID,
                                "instance_id": _SERVICE_INSTANCE,
                                "scope_ref": _APP_SCOPE_B_REF,
                                "transport_ref": "telemetry-query-v1",
                            },
                        ],
                        "options": {"request_timeout_ms": _REQUEST_TIMEOUT_MS},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "telemetry-query-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": "telemetry-app-curve-v1",
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    }
                },
            },
        }
    )


def _host_process(control, raw_config, registry_path, keys):
    config = parse_service_config(raw_config)
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    principals = {
        keys["strategy_a_public"]: _PRINCIPAL_A,
        keys["strategy_b_public"]: _PRINCIPAL_B,
        keys["application_public"]: _APP_PRINCIPAL,
    }
    grants = {}
    for principal_ref, strategy_ref, scope_ref, actor_ref in (
        (_PRINCIPAL_A, _STRATEGY_A, _SCOPE_A_REF, _STRATEGY_A),
        (_PRINCIPAL_B, _STRATEGY_B, _SCOPE_B_REF, _STRATEGY_B),
    ):
        grant = ResourceGrant(
            _SERVICE_INSTANCE,
            "service.telemetry_ingress",
            scope_ref,
            WireScope(_DOMAIN, strategy_ref=strategy_ref),
            frozenset({"telemetry.append"}),
        )
        grants[principal_ref] = (
            PrincipalGrant(
                WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, actor_ref), (grant,)
            ),
        )
    app_grants = tuple(
        ResourceGrant(
            _SERVICE_INSTANCE,
            "service.monitor_query",
            scope_ref,
            WireScope(_DOMAIN, strategy_ref=strategy_ref),
            frozenset({"telemetry.snapshot"}),
        )
        for scope_ref, strategy_ref in (
            (_APP_SCOPE_A_REF, _STRATEGY_A),
            (_APP_SCOPE_B_REF, _STRATEGY_B),
        )
    )
    grants[_APP_PRINCIPAL] = (
        PrincipalGrant(
            WireIdentity(WireRole.APPLICATION_CLIENT, _APP_PRINCIPAL, _APP_ID), app_grants
        ),
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="telemetry-service-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="telemetry-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="telemetry-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals=principals,
        grants=grants,
    )

    def resource_provider(component, _instance_id, _options, _scope):
        if component != "monitor":
            raise AssertionError("unexpected service component")
        return ComponentResources.borrowed(
            {"targets": frozenset({_SCOPE_A, _SCOPE_B})}, ready=lambda: True
        )

    host = None
    try:
        host = ServiceHost(config, authority, registry_path, resource_provider)
        host.start()
        control.send(("ready", host.endpoint, host.state))
        while True:
            host.serve_once(timeout_ms=25)
            if not control.poll():
                continue
            command = control.recv()
            if command == ("close",):
                host.close()
                control.send(("closed", host.state))
                return
            raise AssertionError("unknown local telemetry host command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _scope_imports_absent() -> None:
    loaded = tuple(
        name
        for name in sys.modules
        if any(name == package or name.startswith(f"{package}.") for package in _DOMAIN_PACKAGES)
    )
    assert not loaded, loaded


def _forged_scope_request(runtime, endpoint: str, keys: dict[str, bytes]) -> None:
    binding = runtime.bindings[0]
    peer = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    profile = {
        "backend": "zmq",
        "endpoints": {"rpc_connect": endpoint},
        "security_profile_ref": "strategy-curve-v1",
        "options": {
            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
            "high_water_mark": 32,
            "max_message_bytes": _MAX_MESSAGE_BYTES,
        },
    }
    transport = ZmqTransportFactory(
        {
            "strategy-curve-v1": ClientCurveKeys(
                keys["server_public"],
                keys["strategy_a_public"],
                keys["strategy_a_secret"],
            )
        }
    )(profile, peer)
    sample = {
        "schema_version": 1,
        "stream_id": uuid4().hex,
        "sequence": 1,
        "kind": "metrics",
        "observed_at": time.time(),
        "metrics": {"cpu_pct": 1.0},
        "dropped_queue_total": 0,
        "dropped_delivery_total": 0,
    }
    request = WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid4().hex,
            correlation_id=None,
            identity=runtime.identity,
            scope=WireScope(_DOMAIN, strategy_ref=_STRATEGY_B, run_id=runtime.run_id),
            operation="telemetry.append",
            schema_id="sdk.service.call",
            schema_version=1,
            sent_at_utc=datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        ),
        {
            "registration_id": binding.registration_id,
            "service_epoch": binding.service_epoch,
            "instance_id": binding.instance_id,
            "arguments": sample,
        },
    )
    try:
        reply = transport.request(request, timeout=_REQUEST_TIMEOUT_MS / 1000)
        assert reply.payload["ok"] is False
        assert reply.payload["code"] == "SCOPE_DENIED"
    finally:
        transport.close()


def _strategy_process(
    control,
    endpoint,
    strategy_ref,
    principal_ref,
    scope_ref,
    security_ref,
    strategy_public,
    strategy_secret,
    server_public,
):
    runtime = None
    blocker = _DomainImportBlocker()
    try:
        _scope_imports_absent()
        sys.meta_path.insert(0, blocker)
        config = _strategy_config(endpoint, strategy_ref, scope_ref, security_ref)
        peer = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda _ref: WireScope(_DOMAIN, strategy_ref=strategy_ref),
            peer_resolver=lambda _service: peer,
            transport_factory=ZmqTransportFactory(
                {security_ref: ClientCurveKeys(server_public, strategy_public, strategy_secret)}
            ),
        )
        startup = runtime.start()
        assert startup.degraded_count == 0
        assert len(runtime.bindings) == 1
        binding = runtime.bindings[0]
        assert binding.operations == frozenset({"telemetry.append"})
        assert binding.required is False
        emitter = ServiceTelemetryEmitter.from_config(runtime, config, scope_ref=scope_ref)
        assert emitter.stats.state == "ready"

        call_count = 0
        actual_call = runtime.call

        def counted_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return actual_call(*args, **kwargs)

        runtime.call = counted_call
        control.send(("ready", runtime.run_id, emitter.stream_id, binding.operations))
        while True:
            command = control.recv()
            if command == ("produce",):
                before = call_count
                assert emitter.enqueue_heartbeat()
                assert emitter.enqueue_metrics({"cpu_pct": 12.5, "latency_ms": 7.0})
                assert emitter.enqueue_metrics({"cpu_pct": 99.0}) is False
                queued = emitter.stats
                assert call_count == before == 0
                assert queued.queued_events == 2
                assert queued.dropped_queue_total == 1
                flushed = emitter.flush(max_events=2)
                assert (flushed.attempted, flushed.delivered, flushed.dropped, flushed.ready) == (
                    2,
                    2,
                    0,
                    True,
                )
                delivered = emitter.stats
                assert call_count == 2
                assert delivered.queued_events == 0
                assert delivered.delivered_total == 2
                _scope_imports_absent()
                control.send(
                    (
                        "produced",
                        runtime.run_id,
                        emitter.stream_id,
                        call_count,
                        queued.dropped_queue_total,
                        delivered.delivered_total,
                        delivered.dropped_delivery_total,
                        delivered.state,
                    )
                )
            elif command == ("metrics_only",):
                before = call_count
                assert emitter.enqueue_metrics({"cpu_pct": 7.5})
                assert call_count == before == 0
                flushed = emitter.flush(max_events=1)
                assert (flushed.attempted, flushed.delivered, flushed.dropped) == (1, 1, 0)
                _scope_imports_absent()
                control.send(("metrics_only", runtime.run_id, emitter.stream_id, call_count))
            elif command == ("heartbeat",):
                before = call_count
                assert emitter.enqueue_heartbeat()
                assert call_count == before
                flushed = emitter.flush(max_events=1)
                assert (flushed.attempted, flushed.delivered, flushed.dropped) == (1, 1, 0)
                _scope_imports_absent()
                control.send(("heartbeat", call_count, emitter.stats.delivered_total))
            elif command == ("forged_scope",):
                _forged_scope_request(
                    runtime,
                    endpoint,
                    {
                        "server_public": server_public,
                        "strategy_a_public": strategy_public,
                        "strategy_a_secret": strategy_secret,
                    },
                )
                _scope_imports_absent()
                control.send(("forged_scope_denied",))
            elif command == ("after_host_stop",):
                assert emitter.enqueue_metrics({"cpu_pct": 15.0})
                failed = emitter.flush(max_events=1)
                assert (failed.attempted, failed.delivered, failed.dropped) == (1, 0, 1)
                dropped = emitter.stats
                assert dropped.queued_events == 0
                assert dropped.dropped_delivery_total == 1
                assert dropped.state == "degraded"
                empty = emitter.flush(max_events=1)
                assert (empty.attempted, empty.delivered, empty.dropped) == (0, 0, 0)
                assert empty.ready is False
                assert emitter.stats.dropped_delivery_total == 1
                control.send(
                    (
                        "after_host_stop",
                        failed.attempted,
                        failed.delivered,
                        failed.dropped,
                        empty.attempted,
                        emitter.stats.dropped_delivery_total,
                        emitter.stats.last_error_code,
                        call_count,
                    )
                )
            elif command == ("close",):
                emitter.close()
                runtime.close()
                control.send(
                    (
                        "closed",
                        runtime.state,
                        runtime.cleanup_failures,
                        runtime.unregister_uncertainties,
                        runtime.healthy,
                    )
                )
                return
            else:
                raise AssertionError("unknown strategy telemetry command")
    except BaseException as exc:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        if blocker in sys.meta_path:
            sys.meta_path.remove(blocker)
        control.close()


def _plain(value: Any) -> Any:
    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _snapshot(runtime, scope_ref: str) -> dict[str, Any]:
    return _plain(
        runtime.call(
            "application.monitor",
            "telemetry.snapshot",
            {},
            scope_ref=scope_ref,
        )
    )


def test_spawned_strategy_telemetry_is_scoped_bounded_and_heartbeat_only(
    tmp_path,
) -> None:
    # Keep the existing process cleanup helpers out of the strategy child's
    # module-import path; that helper module imports Gateway implementation.
    from tests.unit.test_service_account_integration import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _client_command,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_a_public, strategy_a_secret = zmq.curve_keypair()
    strategy_b_public, strategy_b_secret = zmq.curve_keypair()
    application_public, application_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_a_public": strategy_a_public,
        "strategy_a_secret": strategy_a_secret,
        "strategy_b_public": strategy_b_public,
        "strategy_b_secret": strategy_b_secret,
        "application_public": application_public,
    }
    service_raw = _service_config(endpoint)
    service_registry = str(tmp_path / "telemetry-registry.sqlite3")
    host_record = None
    strategy_a_record = None
    strategy_b_record = None
    application_runtime = None
    records = []
    forced = []
    failures = []
    try:
        host_record = _start_process(
            context,
            _host_process,
            (service_raw, service_registry, keys),
            "telemetry-monitor-host-r1",
        )
        records.append(host_record)
        host_ready = _receive(host_record["pipe"])
        assert host_ready[0] == "ready", host_ready
        assert host_ready[1] == endpoint
        assert host_ready[2] == "started"

        strategy_a_record = _start_process(
            context,
            _strategy_process,
            (
                endpoint,
                _STRATEGY_A,
                _PRINCIPAL_A,
                _SCOPE_A_REF,
                "strategy-a-curve-v1",
                strategy_a_public,
                strategy_a_secret,
                server_public,
            ),
            "telemetry-strategy-a-r1",
        )
        records.append(strategy_a_record)
        strategy_b_record = _start_process(
            context,
            _strategy_process,
            (
                endpoint,
                _STRATEGY_B,
                _PRINCIPAL_B,
                _SCOPE_B_REF,
                "strategy-b-curve-v1",
                strategy_b_public,
                strategy_b_secret,
                server_public,
            ),
            "telemetry-strategy-b-r1",
        )
        records.append(strategy_b_record)
        strategy_a_ready = _receive(strategy_a_record["pipe"])
        strategy_b_ready = _receive(strategy_b_record["pipe"])
        assert strategy_a_ready[0] == strategy_b_ready[0] == "ready"
        assert strategy_a_ready[1] != strategy_b_ready[1]
        assert strategy_a_ready[2] != strategy_b_ready[2]
        assert strategy_a_ready[3] == strategy_b_ready[3] == frozenset({"telemetry.append"})

        app_config = _application_config(endpoint)
        app_identity = WireIdentity(WireRole.APPLICATION_CLIENT, _APP_PRINCIPAL, _APP_ID)
        service_peer = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        app_scopes = {_APP_SCOPE_A_REF: _SCOPE_A, _APP_SCOPE_B_REF: _SCOPE_B}
        application_runtime = ServiceClientRuntime(
            app_config,
            identity=app_identity,
            scope_resolver=lambda ref: app_scopes[ref],
            peer_resolver=lambda _service: service_peer,
            transport_factory=ZmqTransportFactory(
                {
                    "telemetry-app-curve-v1": ClientCurveKeys(
                        server_public, application_public, application_secret
                    )
                }
            ),
            requested_operations={"application.monitor": frozenset({"telemetry.snapshot"})},
        )
        assert application_runtime.start().degraded_count == 0
        assert len(application_runtime.bindings) == 2
        initial_a = _snapshot(application_runtime, _APP_SCOPE_A_REF)
        initial_b = _snapshot(application_runtime, _APP_SCOPE_B_REF)
        assert initial_a == {"state": "not_collected", "streams": []}
        assert initial_b == {"state": "not_collected", "streams": []}

        produced_a = _client_command(strategy_a_record, "produce")
        produced_b = _client_command(strategy_b_record, "metrics_only")
        assert produced_a[0] == "produced"
        assert produced_a[1] == strategy_a_ready[1]
        assert produced_a[2] == strategy_a_ready[2]
        assert produced_a[3:] == (2, 1, 2, 0, "ready")
        assert produced_b == ("metrics_only", strategy_b_ready[1], strategy_b_ready[2], 1)
        with pytest.raises(ServiceRuntimeError) as oversized:
            application_runtime.call(
                "application.monitor",
                "telemetry.snapshot",
                {},
                scope_ref=_APP_SCOPE_A_REF,
                max_reply_message_bytes=1024,
            )
        assert type(oversized.value) is ServiceRuntimeError
        assert oversized.value.code == "REPLY_TOO_LARGE"

        snapshot_a = _snapshot(application_runtime, _APP_SCOPE_A_REF)
        snapshot_b = _snapshot(application_runtime, _APP_SCOPE_B_REF)
        assert snapshot_a["state"] == "collected"
        assert len(snapshot_a["streams"]) == 1
        stream_a = snapshot_a["streams"][0]
        assert stream_a["producer"]["principal_ref"] == _PRINCIPAL_A
        assert stream_a["scope"]["execution_domain_ref"] == _DOMAIN
        assert stream_a["scope"]["strategy_ref"] == _STRATEGY_A
        assert stream_a["scope"]["run_id"] == strategy_a_ready[1]
        assert stream_a["status"] == "fresh"
        assert stream_a["heartbeat"]["kind"] == "heartbeat"
        assert stream_a["metrics"]["kind"] == "metrics"
        assert stream_a["metrics"]["metrics"] == {"cpu_pct": 12.5, "latency_ms": 7.0}
        assert stream_a["metrics"]["dropped_queue_total"] == 1

        assert snapshot_b["state"] == "collected"
        assert len(snapshot_b["streams"]) == 1
        stream_b = snapshot_b["streams"][0]
        assert stream_b["producer"]["principal_ref"] == _PRINCIPAL_B
        assert stream_b["scope"]["strategy_ref"] == _STRATEGY_B
        assert stream_b["scope"]["run_id"] == strategy_b_ready[1]
        assert stream_b["status"] == "not_collected"
        assert stream_b["heartbeat"] is None
        assert stream_b["metrics"]["metrics"] == {"cpu_pct": 7.5}

        assert _client_command(strategy_a_record, "forged_scope") == ("forged_scope_denied",)
        heartbeat = _client_command(strategy_a_record, "heartbeat")
        assert heartbeat == ("heartbeat", 3, 3)
        refreshed_a = _snapshot(application_runtime, _APP_SCOPE_A_REF)["streams"][0]
        assert refreshed_a["status"] == "fresh"
        assert refreshed_a["metrics"]["metrics"] == {"cpu_pct": 12.5, "latency_ms": 7.0}
        assert refreshed_a["metrics"]["sequence"] == 2
        assert refreshed_a["heartbeat"]["sequence"] == 3

        time.sleep(2.2)
        stale_a = _snapshot(application_runtime, _APP_SCOPE_A_REF)["streams"][0]
        assert stale_a["status"] == "stale"
        assert stale_a["metrics"]["metrics"] == {"cpu_pct": 12.5, "latency_ms": 7.0}

        closed_b = _close_normally(strategy_b_record, expected_tag="closed")
        assert closed_b[1] == "closed"
        assert closed_b[2] == 0
        assert closed_b[3] == 0
        application_runtime.close()
        assert application_runtime.state == "closed"
        application_runtime = None
        assert _close_normally(host_record, expected_tag="closed") == ("closed", "closed")

        failed_delivery = _client_command(strategy_a_record, "after_host_stop")
        assert failed_delivery[0] == "after_host_stop"
        assert failed_delivery[1:6] == (1, 0, 1, 0, 1)
        assert failed_delivery[6] is not None
        closed_a = _close_normally(strategy_a_record, expected_tag="closed")
        assert closed_a[1] == "closed"
    finally:
        if application_runtime is not None:
            with suppress(BaseException):
                application_runtime.close()
        for record in records:
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
