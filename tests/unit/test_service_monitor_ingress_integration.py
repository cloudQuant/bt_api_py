"""Durable monitor ingress over real spawned ServiceHosts and CURVE RPC."""

from __future__ import annotations

import multiprocessing
import socket
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
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.service_component import MonitorReadTarget
from bt_api_monitor.service_ingress import MonitorIngressTarget

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.unit.test_service_account_integration import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)

_INGRESS_ID = "synthetic-monitor-ingress"
_INGRESS_PRINCIPAL = "synthetic-monitor-ingress-principal"
_INGRESS_INSTANCE = "monitor-ingress"
_INGRESS_SCOPE_REF = "monitor-ingress-account-v1"
_INGRESS_SECURITY_REF = "monitor-ingress-server-curve-v1"
_READ_ID = "synthetic-monitor-reader"
_READ_PRINCIPAL = "synthetic-monitor-reader-principal"
_READ_INSTANCE = "monitor-read"
_READ_SCOPE_REF = "monitor-read-account-v1"
_READ_SECURITY_REF = "monitor-read-server-curve-v1"
_APPLICATION_PRINCIPAL = "synthetic-monitor-application"
_APPLICATION_ID = "synthetic-monitor-dashboard"
_DOMAIN = "paper:monitor-ingress"
_ACCOUNT = "synthetic-account"
_OUTBOX_SCOPE = "outbox:synthetic-account"
_EVENT_TYPE = "account.observation"
_ACCOUNT_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT)
_REQUEST_TIMEOUT_MS = 2_000
_HEARTBEAT_SECONDS = 5
_MAX_MESSAGE_BYTES = 65_536
_INGRESS_LIMITS = {"max_events": 2, "max_bytes": 8192, "max_event_bytes": 2048}


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _component(instance_id: str, port: str, options: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {"instance_id": instance_id, "bind_ports": [port], "options": options},
    }


def _server_config(*, ingress: bool, endpoint: str) -> dict[str, Any]:
    if ingress:
        service_id = _INGRESS_ID
        instance_id = _INGRESS_INSTANCE
        port = "service.telemetry_ingress"
        options = {"role": "durable_ingress", **_INGRESS_LIMITS}
    else:
        service_id = _READ_ID
        instance_id = _READ_INSTANCE
        port = "service.monitor_query"
        options = {}
    security_ref = _INGRESS_SECURITY_REF if ingress else _READ_SECURITY_REF
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service_id,
        "scope_ref": f"{service_id}-root-v1",
        "transport_ref": "listener-v1",
        "registration_policy_ref": f"{service_id}-registration-v1",
        "service_requirements_ref": f"{service_id}-requirements-v1",
        "components": {"monitor": _component(instance_id, port, options)},
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": security_ref,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _authority(
    config,
    *,
    service_identity: WireIdentity,
    scope: WireScope,
    server_public: bytes,
    server_secret: bytes,
    principals: dict[bytes, str],
    grants: dict[str, tuple[PrincipalGrant, ...]],
    security_ref: str,
) -> TrustedServiceAuthority:
    return TrustedServiceAuthority(
        identity=service_identity,
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
        principals=principals,
        grants=grants,
    )


def _host_process(control, raw_config, registry_path, outbox_path, keys, ingress: bool):
    config = parse_service_config(raw_config)
    if ingress:
        service_identity = WireIdentity(WireRole.SERVICE, _INGRESS_PRINCIPAL, _INGRESS_ID)
        security_ref = _INGRESS_SECURITY_REF
        principals = {
            keys["producer_a_public"]: "synthetic-producer-a",
            keys["producer_b_public"]: "synthetic-producer-b",
        }
        grants = {}
        for principal_ref, actor in (
            ("synthetic-producer-a", "producer-a-service"),
            ("synthetic-producer-b", "producer-b-service"),
        ):
            grant = ResourceGrant(
                _INGRESS_INSTANCE,
                "service.telemetry_ingress",
                _INGRESS_SCOPE_REF,
                _ACCOUNT_SCOPE,
                frozenset({"events.append"}),
            )
            grants[principal_ref] = (
                PrincipalGrant(WireIdentity(WireRole.SERVICE, principal_ref, actor), (grant,)),
            )

        def resources(component, _instance, _options, _scope):
            if component != "monitor":
                raise AssertionError("durable ingress selected an unexpected component")
            outbox = DurableOutbox(outbox_path)
            return ComponentResources.borrowed(
                {
                    "outbox": outbox,
                    "targets": {
                        _ACCOUNT_SCOPE: MonitorIngressTarget(_OUTBOX_SCOPE, (_EVENT_TYPE,))
                    },
                },
                ready=lambda: True,
            )

    else:
        service_identity = WireIdentity(WireRole.SERVICE, _READ_PRINCIPAL, _READ_ID)
        security_ref = _READ_SECURITY_REF
        principals = {keys["application_public"]: _APPLICATION_PRINCIPAL}
        grant = ResourceGrant(
            _READ_INSTANCE,
            "service.monitor_query",
            _READ_SCOPE_REF,
            _ACCOUNT_SCOPE,
            frozenset({"monitor.read_after"}),
        )
        grants = {
            _APPLICATION_PRINCIPAL: (
                PrincipalGrant(
                    WireIdentity(
                        WireRole.APPLICATION_CLIENT, _APPLICATION_PRINCIPAL, _APPLICATION_ID
                    ),
                    (grant,),
                ),
            )
        }

        def resources(component, _instance, _options, _scope):
            if component != "monitor":
                raise AssertionError("monitor query selected an unexpected component")
            outbox = DurableOutbox(outbox_path, read_only=True)
            return ComponentResources.borrowed(
                {
                    "outbox": outbox,
                    "targets": {_ACCOUNT_SCOPE: MonitorReadTarget(_OUTBOX_SCOPE, (_EVENT_TYPE,))},
                },
                ready=lambda: outbox.is_read_only,
            )

    host = None
    try:
        host = ServiceHost(
            config,
            _authority(
                config,
                service_identity=service_identity,
                scope=_ACCOUNT_SCOPE,
                server_public=keys["server_public"],
                server_secret=keys["server_secret"],
                principals=principals,
                grants=grants,
                security_ref=security_ref,
            ),
            registry_path,
            resources,
        )
        host.start()
        control.send(("ready", host.endpoint))
        while True:
            host.serve_once(timeout_ms=25)
            if not control.poll():
                continue
            command = control.recv()
            if command == ("close",):
                host.close()
                control.send(("closed", host.state))
                return
            raise AssertionError("unknown local test host command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _producer_config(endpoint: str, producer_id: str):
    service_id = f"{producer_id}-service"
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": service_id,
            "scope_ref": f"{producer_id}-root-v1",
            "transport_ref": "producer-listener-v1",
            "registration_policy_ref": f"{producer_id}-policy-v1",
            "service_requirements_ref": f"{producer_id}-requirements-v1",
            "components": {
                "monitor": _component(
                    f"{producer_id}-unused-ingress",
                    "service.telemetry_ingress",
                    {"role": "durable_ingress", **_INGRESS_LIMITS},
                )
            },
            "clients": {
                "monitor": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "upstream.telemetry",
                                "service_id": _INGRESS_ID,
                                "instance_id": _INGRESS_INSTANCE,
                                "scope_ref": _INGRESS_SCOPE_REF,
                                "transport_ref": "ingress-connect-v1",
                            }
                        ],
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                        },
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "producer-listener-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": _endpoint()},
                        "security_profile_ref": "producer-unused-server-v1",
                        "options": {"high_water_mark": 32, "max_message_bytes": _MAX_MESSAGE_BYTES},
                    },
                    "ingress-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": "producer-client-v1",
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    },
                },
            },
        }
    )


def _producer_runtime(endpoint: str, keys: dict[str, bytes], producer_id: str):
    config = _producer_config(endpoint, producer_id)
    key_prefix = producer_id.replace("-", "_")
    principal_ref = f"synthetic-{producer_id}"
    identity = WireIdentity(WireRole.SERVICE, principal_ref, f"{producer_id}-service")
    peer = WireIdentity(WireRole.SERVICE, _INGRESS_PRINCIPAL, _INGRESS_ID)
    runtime = ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _ref: _ACCOUNT_SCOPE,
        peer_resolver=lambda _service: peer,
        transport_factory=ZmqTransportFactory(
            {
                "producer-client-v1": ClientCurveKeys(
                    keys["server_public"],
                    keys[f"{key_prefix}_public"],
                    keys[f"{key_prefix}_secret"],
                )
            }
        ),
        requested_operations={"upstream.telemetry": frozenset({"events.append"})},
    )
    result = runtime.start()
    assert result.degraded_count == 0
    assert runtime.bindings[0].operations == frozenset({"events.append"})
    return runtime


def _application_runtime(endpoint: str, keys: dict[str, bytes]):
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, _APPLICATION_PRINCIPAL, _APPLICATION_ID)
    peer = WireIdentity(WireRole.SERVICE, _READ_PRINCIPAL, _READ_ID)
    config = parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "application_client",
            "registration": {"application_ref": _APPLICATION_ID},
            "clients": {
                "monitor": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "application.monitor",
                                "service_id": _READ_ID,
                                "instance_id": _READ_INSTANCE,
                                "scope_ref": _READ_SCOPE_REF,
                                "transport_ref": "read-connect-v1",
                            }
                        ],
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                        },
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "read-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": "application-client-v1",
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
    return ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _ref: _ACCOUNT_SCOPE,
        peer_resolver=lambda _service: peer,
        transport_factory=ZmqTransportFactory(
            {
                "application-client-v1": ClientCurveKeys(
                    keys["server_public"],
                    keys["application_public"],
                    keys["application_secret"],
                )
            }
        ),
        requested_operations={"application.monitor": frozenset({"monitor.read_after"})},
    )


def _event(source_event_id: str, value: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": source_event_id,
        "event_type": _EVENT_TYPE,
        "occurred_at": 1_800_000_000.0,
        "data": {"fact": "synthetic-balance", "value": value},
    }


def _write(runtime: ServiceClientRuntime, request: dict[str, Any]) -> dict[str, Any]:
    return dict(runtime.call("upstream.telemetry", "events.append", request))


def _events(runtime: ServiceClientRuntime) -> tuple[dict[str, Any], ...]:
    page = runtime.call(
        "application.monitor", "monitor.read_after", {"after_sequence": 0, "limit": 20}
    )
    assert page["requested_cursor"] == 0
    assert page["next_cursor"] == len(page["events"])
    assert page["has_more"] is False
    return tuple(dict(event) for event in page["events"])


def _assert_handler_outcome_unknown(runtime: ServiceClientRuntime, request: dict[str, Any]):
    with pytest.raises(ServiceCallOutcomeUnknownError) as error:
        _write(runtime, request)
    assert error.value.code == "BUSINESS_OUTCOME_UNKNOWN"


def _assert_forged_scope_denied(
    runtime: ServiceClientRuntime,
    endpoint: str,
    keys: dict[str, bytes],
    request: dict[str, Any],
) -> None:
    binding = runtime.bindings[0]
    identity = runtime.identity
    peer = WireIdentity(WireRole.SERVICE, _INGRESS_PRINCIPAL, _INGRESS_ID)
    profile = {
        "backend": "zmq",
        "endpoints": {"rpc_connect": endpoint},
        "security_profile_ref": "producer-client-v1",
        "options": {
            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
            "high_water_mark": 32,
            "max_message_bytes": _MAX_MESSAGE_BYTES,
        },
    }
    transport = ZmqTransportFactory(
        {
            "producer-client-v1": ClientCurveKeys(
                keys["server_public"], keys["producer_a_public"], keys["producer_a_secret"]
            )
        }
    )(profile, peer)
    message_id = uuid4().hex
    forged_scope = WireScope(_DOMAIN, account_ref="forged-account")
    envelope = WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=message_id,
            correlation_id=None,
            identity=identity,
            scope=forged_scope,
            operation="events.append",
            schema_id="sdk.service.call",
            schema_version=1,
            sent_at_utc=datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        ),
        {
            "registration_id": binding.registration_id,
            "service_epoch": binding.service_epoch,
            "instance_id": binding.instance_id,
            "arguments": request,
        },
    )
    try:
        reply = transport.request(envelope, timeout=2.0)
        assert reply.payload["ok"] is False
        assert reply.payload["code"] == "SCOPE_DENIED"
    finally:
        transport.close()


def test_spawned_curve_monitor_ingress_is_deduplicated_bounded_and_survives_restart(
    tmp_path,
) -> None:
    context = multiprocessing.get_context("spawn")
    outbox_path = str(tmp_path / "monitor-outbox.sqlite3")
    ingress_registry = str(tmp_path / "monitor-ingress-registry.sqlite3")
    reader_registry = str(tmp_path / "monitor-reader-registry.sqlite3")
    ingress_endpoint = _endpoint()
    reader_endpoint = _endpoint()
    ingress_server_public, ingress_server_secret = zmq.curve_keypair()
    read_server_public, read_server_secret = zmq.curve_keypair()
    producer_a_public, producer_a_secret = zmq.curve_keypair()
    producer_b_public, producer_b_secret = zmq.curve_keypair()
    application_public, application_secret = zmq.curve_keypair()
    keys = {
        "server_public": ingress_server_public,
        "server_secret": ingress_server_secret,
        "producer_a_public": producer_a_public,
        "producer_a_secret": producer_a_secret,
        "producer_b_public": producer_b_public,
        "producer_b_secret": producer_b_secret,
        "application_public": application_public,
        "application_secret": application_secret,
    }
    ingress_raw = _server_config(ingress=True, endpoint=ingress_endpoint)
    read_raw = _server_config(ingress=False, endpoint=reader_endpoint)
    ingress_record = _start_process(
        context,
        _host_process,
        (
            ingress_raw,
            ingress_registry,
            outbox_path,
            keys,
            True,
        ),
        "monitor-ingress-host-r1",
    )
    read_keys = {
        "server_public": read_server_public,
        "server_secret": read_server_secret,
        "application_public": application_public,
        "application_secret": application_secret,
    }
    reader_record = None
    restart_record = None
    producer_a = producer_b = restarted_a = retry_a = app_runtime = None
    forced: list[str] = []
    cleanup_failures: list[str] = []
    try:
        started = _receive(ingress_record["pipe"])
        assert started[0] == "ready", started
        ingress_endpoint = started[1]

        reader_record = _start_process(
            context,
            _host_process,
            (read_raw, reader_registry, outbox_path, read_keys, False),
            "monitor-query-host",
        )
        reader_started = _receive(reader_record["pipe"])
        assert reader_started[0] == "ready", reader_started
        reader_endpoint = reader_started[1]

        producer_a = _producer_runtime(ingress_endpoint, keys, "producer-a")
        first_request = _event("source-event-shared", "100.00")
        first = _write(producer_a, first_request)
        retry = _write(producer_a, first_request)
        assert first == retry
        assert first["source_event_id"] == "source-event-shared"
        assert first["sequence"] == 1
        assert first["event_id"].startswith("service-event:")

        _assert_forged_scope_denied(producer_a, ingress_endpoint, keys, first_request)
        _assert_handler_outcome_unknown(producer_a, _event("source-event-shared", "999.00"))
        producer_a.close()
        producer_a = None

        producer_b = _producer_runtime(ingress_endpoint, keys, "producer-b")
        second = _write(producer_b, first_request)
        assert second["source_event_id"] == first["source_event_id"]
        assert second["sequence"] == 2
        assert second["event_id"].startswith("service-event:")
        assert second["event_id"] != first["event_id"]
        producer_b.close()
        producer_b = None

        app_runtime = _application_runtime(reader_endpoint, read_keys)
        app_runtime.start()
        assert app_runtime.bindings[0].operations == frozenset({"monitor.read_after"})
        before_restart = _events(app_runtime)
        assert len(before_restart) == 2

        _close_normally(ingress_record, expected_tag="closed")
        ingress_record = None
        ingress_endpoint = _endpoint()
        ingress_raw = _server_config(ingress=True, endpoint=ingress_endpoint)
        restart_record = _start_process(
            context,
            _host_process,
            (ingress_raw, ingress_registry, outbox_path, keys, True),
            "monitor-ingress-host-r2",
        )
        restarted = _receive(restart_record["pipe"])
        assert restarted[0] == "ready", restarted
        ingress_endpoint = restarted[1]

        restarted_a = _producer_runtime(ingress_endpoint, keys, "producer-a")
        after_restart = _write(restarted_a, first_request)
        assert after_restart == first
        _assert_handler_outcome_unknown(restarted_a, _event("source-event-over-capacity", "3"))
        restarted_a.close()
        restarted_a = None

        retry_a = _producer_runtime(ingress_endpoint, keys, "producer-a")
        retry_at_capacity = _write(retry_a, first_request)
        assert retry_at_capacity == first
        retry_a.close()
        retry_a = None

        persisted = _events(app_runtime)
        assert len(persisted) == 2
        assert [event["sequence"] for event in persisted] == [1, 2]
        assert {event["event_id"] for event in persisted} == {
            first["event_id"],
            second["event_id"],
        }
        by_actor = {event["data"]["producer"]["actor_ref"]: event for event in persisted}
        assert set(by_actor) == {"producer-a-service", "producer-b-service"}
        assert {
            actor: event["data"]["producer"]["principal_ref"] for actor, event in by_actor.items()
        } == {
            "producer-a-service": "synthetic-producer-a",
            "producer-b-service": "synthetic-producer-b",
        }
        assert all(
            event["data"]["source_event_id"] == "source-event-shared"
            and event["event_type"] == _EVENT_TYPE
            and event["data"]["payload"] == {"fact": "synthetic-balance", "value": "100.00"}
            for event in persisted
        )
        app_runtime.close()
        app_runtime = None

        _close_normally(restart_record, expected_tag="closed")
        restart_record = None
        _close_normally(reader_record, expected_tag="closed")
        reader_record = None
    finally:
        for runtime in (producer_a, producer_b, restarted_a, retry_a, app_runtime):
            if runtime is not None:
                try:
                    runtime.close()
                except Exception as exc:
                    cleanup_failures.append(f"runtime_close:{type(exc).__name__}")
        for record in (restart_record, reader_record, ingress_record):
            if record is not None:
                _bounded_reap_spawned_process(record, forced, cleanup_failures)
        _assert_no_fallback_cleanup(forced, cleanup_failures)
