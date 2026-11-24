"""Monitor observation pages use one exact application-bound SDK route."""

from __future__ import annotations

import hashlib
import queue
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import replace

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.observation_query import (
    sign_observation_query_request,
    verify_observation_query_page,
)
from bt_api_monitor.service_component import MonitorReadTarget

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    BoundMonitorQueryTarget,
    BoundServiceTarget,
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeRequestNotSentError,
    ZmqTransportFactory,
    _validate_bound_target_operation,
)
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime

pytestmark = pytest.mark.skipif(not zmq.has("curve"), reason="CURVE unavailable")

_SERVICE_ID = "monitor-query-runtime-service"
_INSTANCE_ID = "monitor-query-runtime-instance"
_SCOPE_REF = "monitor-query-runtime-scope"
_SECURITY_REF = "monitor-query-runtime-curve"
_TRANSPORT_REF = "monitor-query-runtime-transport"
_APP_PRINCIPAL = "monitor-query-runtime-application"
_APP_ACTOR = "monitor-query-runtime-dashboard"
_SERVICE_PRINCIPAL = "monitor-query-runtime-service-principal"
_DOMAIN = "paper:monitor-query-runtime"
_SCOPE = WireScope(_DOMAIN, account_ref="synthetic-account", strategy_ref="synthetic-strategy")
_EVENT_TYPE = "account.observation.synthetic"
_HMAC_KEY = b"synthetic-monitor-observation-hmac-key-v1"


def _free_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _json_ready(value):
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _service_config(endpoint: str):
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": _SERVICE_ID,
            "scope_ref": _SCOPE_REF,
            "transport_ref": _TRANSPORT_REF,
            "registration_policy_ref": "monitor-query-runtime-registration",
            "service_requirements_ref": "monitor-query-runtime-requirements",
            "components": {
                "monitor": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": _INSTANCE_ID,
                        "bind_ports": ["service.monitor_query"],
                        "options": {
                            "role": "observation_query",
                            "epoch": 1,
                            "retention_floor_sequence": 0,
                        },
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _TRANSPORT_REF: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": endpoint},
                        "security_profile_ref": _SECURITY_REF,
                        "options": {"high_water_mark": 32, "max_message_bytes": 65_536},
                    }
                },
            },
        }
    )


def _client_config(endpoint: str):
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "application_client",
            "registration": {"application_ref": _APP_ACTOR},
            "clients": {
                "monitor": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "application.monitor",
                                "service_id": _SERVICE_ID,
                                "instance_id": _INSTANCE_ID,
                                "scope_ref": _SCOPE_REF,
                                "transport_ref": _TRANSPORT_REF,
                            }
                        ],
                        "options": {
                            "request_timeout_ms": 5_000,
                            "heartbeat_interval_seconds": 5,
                        },
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _TRANSPORT_REF: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _SECURITY_REF,
                        "options": {
                            "request_timeout_ms": 5_000,
                            "high_water_mark": 32,
                            "max_message_bytes": 65_536,
                        },
                    }
                },
            },
        }
    )


def test_monitor_observation_query_requires_exact_bound_target_and_deadline(tmp_path):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    app_identity = WireIdentity(WireRole.APPLICATION_CLIENT, _APP_PRINCIPAL, _APP_ACTOR)
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    config = _service_config(endpoint)
    client_config = _client_config(endpoint)
    outbox_path = tmp_path / "monitor-observations.sqlite3"
    DurableOutbox(outbox_path)
    started: queue.Queue[tuple] = queue.Queue()
    stop = threading.Event()

    grant = ResourceGrant(
        _INSTANCE_ID,
        "service.monitor_query",
        _SCOPE_REF,
        _SCOPE,
        frozenset({"observation.query"}),
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=_SCOPE,
        scope_ref=_SCOPE_REF,
        scope_version="monitor-query-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="monitor-query-curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="monitor-query-registration-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="monitor-query-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: _APP_PRINCIPAL},
        grants={_APP_PRINCIPAL: (PrincipalGrant(app_identity, (grant,)),)},
    )

    def serve_host():
        host = None
        try:
            outbox = DurableOutbox(outbox_path, read_only=True)
            resources = ComponentResources.borrowed(
                {
                    "outbox": outbox,
                    "targets": {_SCOPE: MonitorReadTarget("outbox:synthetic-account", (_EVENT_TYPE,))},
                    "principals": frozenset({_APP_PRINCIPAL}),
                    "hmac_key": _HMAC_KEY,
                },
                ready=lambda: outbox.is_read_only,
            )
            host = ServiceHost(
                config,
                authority,
                tmp_path / "monitor-query-runtime-registry.sqlite3",
                lambda *_: resources,
            )
            host.start()
            started.put(("ready", host.endpoint))
            while not stop.is_set():
                host.serve_once(timeout_ms=10)
        except BaseException as error:
            started.put(("error", getattr(error, "code", type(error).__name__)))
        finally:
            if host is not None and host.state == "started":
                host.close()

    server_thread = threading.Thread(target=serve_host, name="monitor-query-test-host")
    server_thread.start()
    wrapper = None
    try:
        host_started = started.get(timeout=15)
        assert host_started[0] == "ready", host_started
        runtime_run_id = "synthetic-monitor-runtime"

        def runtime_factory():
            return ServiceClientRuntime(
                client_config,
                identity=app_identity,
                scope_resolver=lambda _scope_ref: _SCOPE,
                peer_resolver=lambda _service_id: service_identity,
                transport_factory=ZmqTransportFactory(
                    {
                        _SECURITY_REF: ClientCurveKeys(server_public, client_public, client_secret)
                    }
                ),
                run_id=runtime_run_id,
                requested_operations={"application.monitor": frozenset({"observation.query"})},
            )

        direct_runtime = runtime_factory()
        unsigned = sign_observation_query_request(
            key=_HMAC_KEY,
            principal_ref=_APP_PRINCIPAL,
            scope=_SCOPE,
            event_types=(_EVENT_TYPE,),
            epoch=1,
            page_size=10,
            request_id="monitor-query-request-0001",
            issued_at=time.time(),
        )
        with pytest.raises(ServiceRuntimeRequestNotSentError):
            direct_runtime.call("application.monitor", "observation.query", unsigned)

        wrapper = ThreadedServiceClientRuntime(runtime_factory)
        snapshot = wrapper.start(timeout_seconds=10)
        (binding,) = snapshot.bindings
        target = BoundMonitorQueryTarget(
            "application.monitor",
            "service.monitor_query",
            binding.service_id,
            binding.instance_id,
            binding.scope_ref,
            _SCOPE,
            app_identity,
            runtime_run_id,
            _TRANSPORT_REF,
            _SECURITY_REF,
            host_started[1],
            service_identity,
            hashlib.sha256(server_public).hexdigest(),
            binding.registration_id,
            binding.service_epoch,
        )

        generic_scope = WireScope(
            _DOMAIN,
            account_ref="synthetic-account",
            strategy_ref="synthetic-strategy",
            run_id="synthetic-strategy-run",
        )
        generic_service_target = BoundServiceTarget(
            target.mount,
            target.port,
            target.service_id,
            target.instance_id,
            target.scope_ref,
            generic_scope,
            service_identity,
            generic_scope.run_id,
            target.transport_ref,
            target.security_profile_ref,
            target.endpoint,
            service_identity,
            target.server_key_fingerprint,
            target.registration_id,
            target.service_epoch,
        )
        with pytest.raises(ValueError, match="INVALID_BOUND_OPERATION"):
            _validate_bound_target_operation(generic_service_target, "observation.query")
        with pytest.raises(ValueError, match="INVALID_BOUND_OPERATION"):
            _validate_bound_target_operation(target, "monitor.read_after")
        with pytest.raises(ValueError, match="INVALID_BOUND_MONITOR_QUERY_TARGET"):
            replace(target, identity=service_identity)

        class EqualString:
            def __eq__(self, _other):
                return True

            def __hash__(self):
                return 1

        with pytest.raises(ValueError, match="INVALID_BOUND_MONITOR_QUERY_TARGET"):
            replace(target, mount=EqualString())
        with pytest.raises(ValueError, match="INVALID_BOUND_MONITOR_QUERY_TARGET"):
            replace(target, port=EqualString())

        with pytest.raises(ServiceRuntimeRequestNotSentError):
            wrapper.call("application.monitor", "observation.query", unsigned)
        invalid_targets = (
            replace(target, identity=WireIdentity(WireRole.APPLICATION_CLIENT, "other-principal", _APP_ACTOR)),
            replace(target, scope=WireScope(_DOMAIN, account_ref="other-account",
                                            strategy_ref="synthetic-strategy")),
            replace(target, expected_peer=WireIdentity(WireRole.SERVICE, "other-service-principal", "other-service")),
            replace(target, server_key_fingerprint=hashlib.sha256(b"different-server-key").hexdigest()),
            replace(target, registration_id="stale-monitor-query-registration"),
            replace(target, service_epoch="stale-monitor-query-epoch"),
        )
        for invalid_target in invalid_targets:
            with pytest.raises(ServiceRuntimeRequestNotSentError):
                wrapper.call_bound(
                    invalid_target,
                    "observation.query",
                    unsigned,
                    deadline_monotonic=time.monotonic() + 5.0,
                )
        with pytest.raises(ThreadedRuntimeError, match="TIMEOUT_BEFORE_START"):
            wrapper.call_bound(
                target,
                "observation.query",
                unsigned,
                deadline_monotonic=time.monotonic() - 1.0,
            )

        page = _json_ready(
            wrapper.call_bound(
                target,
                "observation.query",
                unsigned,
                deadline_monotonic=time.monotonic() + 5.0,
            )
        )
        assert page["state"] == "not_collected"
        assert not page["events"]
        assert verify_observation_query_page(
            page,
            key=_HMAC_KEY,
            principal_ref=_APP_PRINCIPAL,
            scope=_SCOPE,
            event_types=(_EVENT_TYPE,),
            epoch=1,
        )
    finally:
        if wrapper is not None:
            wrapper.close(timeout_seconds=10)
        stop.set()
        server_thread.join(timeout=15)
        assert not server_thread.is_alive()
