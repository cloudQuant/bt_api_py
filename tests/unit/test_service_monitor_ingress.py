"""Focused wiring tests for the explicitly opted-in monitor ingress role."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from bt_api_base.service_protocol import (
    WIRE_PROTOCOL_VERSION,
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.service_ingress import (
    MONITOR_INGRESS_OPERATIONS,
    MonitorIngressTarget,
)

from bt_api_py import service_components
from bt_api_py.service_components import (
    ComponentResources,
    ServiceComponentError,
    component_specs,
)
from bt_api_py.service_config import ServiceConfigError, parse_service_config
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.service_runtime import (
    PORT_OPERATIONS,
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ServiceRuntimeError,
)

_OPTIONS = {
    "role": "durable_ingress",
    "max_events": 10_000,
    "max_bytes": 64 * 1024 * 1024,
    "max_event_bytes": 64 * 1024,
}
_BIND_SCOPE = WireScope("paper:demo", account_ref="account-a")
_PRODUCER = WireIdentity(WireRole.SERVICE, "monitor-producer", "producer-service")
_PEER = WireIdentity(WireRole.SERVICE, "monitor-service", "monitor-service")
_SENT_AT = "2026-10-14T00:00:00Z"
_EVENT = {
    "schema_version": 1,
    "event_id": "source-event-1",
    "event_type": "risk.alert",
    "occurred_at": 1_800_000_000.0,
    "data": {"severity": "low"},
}


def _service_config(
    *,
    bind_ports: tuple[str, ...] = ("service.telemetry_ingress",),
    options: dict[str, Any] | None = None,
    unknown_role: str | None = None,
    include_upstream: bool = False,
) -> Any:
    monitor_options = dict(_OPTIONS if options is None else options)
    if unknown_role is not None:
        monitor_options["role"] = unknown_role
    raw: dict[str, Any] = {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "producer-service",
        "scope_ref": "producer-scope-v1",
        "transport_ref": "listener-v1",
        "registration_policy_ref": "policy-v1",
        "service_requirements_ref": "requirements-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "monitor-ingress",
                    "bind_ports": list(bind_ports),
                    "options": monitor_options,
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:7600"},
                    "security_profile_ref": "listener-security-v1",
                }
            },
        },
    }
    if include_upstream:
        raw["clients"] = {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "upstream.telemetry",
                            "service_id": "monitor-service",
                            "instance_id": "monitor-ingress",
                            "scope_ref": "monitor-resource-v1",
                            "transport_ref": "upstream-connect",
                        }
                    ]
                },
            }
        }
        raw["communication"]["profiles"]["upstream-connect"] = {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": "tcp://127.0.0.1:7601"},
            "security_profile_ref": "upstream-security-v1",
        }
    return parse_service_config(raw)


def _request(
    operation: str,
    payload: dict[str, Any],
    *,
    schema_id: str,
    identity: WireIdentity = _PRODUCER,
    scope: WireScope = _BIND_SCOPE,
) -> WireEnvelope:
    _request.next_id += 1
    return WireEnvelope(
        WireHeader(
            protocol_version=WIRE_PROTOCOL_VERSION,
            kind=WireMessageKind.REQUEST,
            message_id=f"monitor-ingress-{_request.next_id}",
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id=schema_id,
            schema_version=1,
            sent_at_utc=_SENT_AT,
        ),
        payload,
    )


_request.next_id = 0


def _build_ingress(config: Any, outbox: DurableOutbox):
    spec = component_specs(config.components)[0]
    prepared = service_components.validate_components((spec,))[0]
    target = MonitorIngressTarget("monitor:account-a", ("risk.alert",))
    resources = ComponentResources.borrowed(
        {"outbox": outbox, "targets": {_BIND_SCOPE: target}}, ready=lambda: True
    )
    component = service_components.build_component(
        prepared,
        scope=_BIND_SCOPE,
        resource_provider=lambda *_: resources,
    )
    return component, target


class _RegistryTransport:
    def __init__(self, registry: ServiceRegistry, peer: WireIdentity) -> None:
        self.registry = registry
        self.peer = peer
        self.requests: list[WireEnvelope] = []
        self.closed = False

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        assert timeout > 0
        self.requests.append(request)
        result = self.registry.handle(_PRODUCER.principal_ref, request)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id=f"reply-{request.header.message_id}",
                correlation_id=request.header.message_id,
                identity=self.peer,
            ),
            result,
        )

    def close(self) -> None:
        self.closed = True


def _registry(component, path: Path) -> ServiceRegistry:
    capability = component.capabilities[0]
    assert capability.write_operations == frozenset()
    return ServiceRegistry(
        path,
        service_id="monitor-service",
        capabilities=component.capabilities,
        grants={
            _PRODUCER.principal_ref: (
                PrincipalGrant(
                    _PRODUCER,
                    (
                        ResourceGrant(
                            capability.instance_id,
                            capability.port,
                            "monitor-resource-v1",
                            _BIND_SCOPE,
                            capability.operations,
                        ),
                    ),
                ),
            )
        },
        policy_version="policy-v1",
        requirements_version="requirements-v1",
    )


def _runtime(config, registry, *, requested_operations=None):
    transport = _RegistryTransport(registry, _PEER)
    runtime = ServiceClientRuntime(
        config,
        identity=_PRODUCER,
        scope_resolver=lambda _reference: _BIND_SCOPE,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=lambda *_: transport,
        requested_operations=requested_operations,
        run_id="producer-run",
    )
    return runtime, transport


def test_configured_ingress_role_is_fixed_and_default_monitor_stays_read_only(tmp_path):
    config = _service_config()
    spec = component_specs(config.components)[0]
    prepared = service_components.validate_components((spec,))[0]
    assert prepared.spec.options["role"] == "durable_ingress"
    assert prepared.options["max_events"] == _OPTIONS["max_events"]
    assert prepared.operations_by_port == {"service.telemetry_ingress": MONITOR_INGRESS_OPERATIONS}
    role_only = _service_config(options={"role": "durable_ingress"})
    role_only_prepared = service_components.validate_components(
        component_specs(role_only.components)
    )[0]
    assert role_only_prepared.options == {
        "role": "durable_ingress",
        "max_events": 10_000,
        "max_bytes": 64 * 1024 * 1024,
        "max_event_bytes": 64 * 1024,
    }

    read_config = _service_config(
        bind_ports=("service.monitor_query",),
        options={},
    )
    read_spec = component_specs(read_config.components)[0]
    read_prepared = service_components.validate_components((read_spec,))[0]
    assert set(read_prepared.operations_by_port["service.monitor_query"]) == {
        "monitor.query",
        "monitor.read_after",
    }
    assert PORT_OPERATIONS["service.telemetry_ingress"] == frozenset(
        {"telemetry.append", "events.append"}
    )

    legacy_ingress = _service_config(bind_ports=("service.telemetry_ingress",), options={})
    with pytest.raises(ServiceComponentError, match="UNSUPPORTED_PUBLIC_PORT"):
        service_components.validate_components(component_specs(legacy_ingress.components))

    with pytest.raises(ServiceConfigError) as exc_info:
        _service_config(unknown_role="untrusted_ingress")
    assert exc_info.value.code == "UNKNOWN_COMPONENT_ROLE"
    with pytest.raises(ServiceConfigError) as exc_info:
        _service_config(bind_ports=("service.monitor_query",))
    assert exc_info.value.code == "PORT_ROLE_MISMATCH"
    with pytest.raises(ServiceConfigError) as exc_info:
        _service_config(options={"role": "durable_ingress", "max_events": 0})
    assert exc_info.value.code == "INVALID_VALUE"


@pytest.mark.parametrize("invalid_resources", ["keys", "target_type"])
def test_ingress_construction_failures_close_owned_resources(tmp_path, invalid_resources):
    config = _service_config()
    prepared = service_components.validate_components(component_specs(config.components))[0]
    outbox = DurableOutbox(tmp_path / f"{invalid_resources}.sqlite3")
    values = {"outbox": outbox}
    if invalid_resources == "target_type":
        values["targets"] = {_BIND_SCOPE: object()}
    closed: list[str] = []
    resources = ComponentResources.owned(
        values,
        ready=lambda: True,
        close=lambda: closed.append("closed"),
    )
    with pytest.raises((ServiceComponentError, ValueError)):
        service_components.build_component(
            prepared,
            scope=_BIND_SCOPE,
            resource_provider=lambda *_: resources,
        )
    assert closed == ["closed"]


def test_service_registry_only_dispatches_explicit_durable_service_event(tmp_path):
    config = _service_config(include_upstream=True)
    outbox = DurableOutbox(tmp_path / "durable-events.sqlite3")
    component, _target = _build_ingress(config, outbox)
    registry = _registry(component, tmp_path / "registry.sqlite3")
    registry.register_handler(
        component.instance_id,
        "events.append",
        component.handlers["events.append"],
    )
    runtime, transport = _runtime(
        config,
        registry,
        requested_operations={"upstream.telemetry": frozenset({"events.append"})},
    )
    try:
        registration = runtime.start()
        assert registration.bindings[0].operations == MONITOR_INGRESS_OPERATIONS
        assert transport.requests[-1].payload["operations"] == ("events.append",)

        wrong_role = WireIdentity(WireRole.STRATEGY_CLIENT, "monitor-producer", "producer-service")
        denied_identity = registry.handle(
            _PRODUCER.principal_ref,
            _request(
                "service.register",
                {
                    "registration_key": "wrong-role-registration",
                    "client_session_id": "wrong-role-session",
                    "instance_id": component.instance_id,
                    "port": "service.telemetry_ingress",
                    "scope_ref": "monitor-resource-v1",
                    "operations": ("events.append",),
                },
                schema_id="sdk.service.control",
                identity=wrong_role,
            ),
        )
        assert denied_identity["code"] == "DENIED"

        wrong_scope = WireScope("paper:other", account_ref="account-a")
        denied_scope = registry.handle(
            _PRODUCER.principal_ref,
            _request(
                "service.register",
                {
                    "registration_key": "wrong-scope-registration",
                    "client_session_id": "wrong-scope-session",
                    "instance_id": component.instance_id,
                    "port": "service.telemetry_ingress",
                    "scope_ref": "monitor-resource-v1",
                    "operations": ("events.append",),
                },
                schema_id="sdk.service.control",
                scope=wrong_scope,
            ),
        )
        assert denied_scope["code"] == "SCOPE_DENIED"

        with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
            runtime.call("upstream.telemetry", "telemetry.append", _EVENT)
        result = runtime.call("upstream.telemetry", "events.append", _EVENT)
        assert result["source_event_id"] == "source-event-1"
        assert result["sequence"] == 1
        rows = outbox.read_pending("test-consumer", "monitor:account-a", limit=10)
        assert len(rows) == 1
        assert rows[0].event.data["source_event_id"] == "source-event-1"
        assert rows[0].event.data["producer"]["role"] == "service"
    finally:
        runtime.close()
        registry.close()
        component.close()


def test_runtime_does_not_expand_legacy_telemetry_registration_to_durable_events(tmp_path):
    config = _service_config(include_upstream=True)
    capability = ServiceCapability(
        "monitor-ingress",
        "service.telemetry_ingress",
        frozenset({"telemetry.append", "events.append"}),
    )
    registry = ServiceRegistry(
        tmp_path / "legacy-runtime-registry.sqlite3",
        service_id="monitor-service",
        capabilities=(capability,),
        grants={
            _PRODUCER.principal_ref: (
                PrincipalGrant(
                    _PRODUCER,
                    (
                        ResourceGrant(
                            "monitor-ingress",
                            "service.telemetry_ingress",
                            "monitor-resource-v1",
                            _BIND_SCOPE,
                            frozenset({"telemetry.append"}),
                        ),
                    ),
                ),
            )
        },
        policy_version="policy-v1",
        requirements_version="requirements-v1",
    )
    registry.register_handler("monitor-ingress", "telemetry.append", lambda *_: {"ok": True})
    runtime, transport = _runtime(config, registry)
    try:
        runtime.start()
        assert runtime.bindings[0].operations == frozenset({"telemetry.append"})
        register = next(
            request
            for request in transport.requests
            if request.header.operation == "service.register"
        )
        assert register.payload["operations"] == ("telemetry.append",)
        with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
            runtime.call("upstream.telemetry", "events.append", _EVENT)
    finally:
        runtime.close()
        registry.close()


def test_new_service_ingress_capability_requires_explicit_requested_operation(tmp_path):
    config = _service_config(include_upstream=True)
    capability = ServiceCapability(
        "monitor-ingress", "service.telemetry_ingress", MONITOR_INGRESS_OPERATIONS
    )
    registry = ServiceRegistry(
        tmp_path / "no-opt-in-registry.sqlite3",
        service_id="monitor-service",
        capabilities=(capability,),
        grants={
            _PRODUCER.principal_ref: (
                PrincipalGrant(
                    _PRODUCER,
                    (
                        ResourceGrant(
                            "monitor-ingress",
                            "service.telemetry_ingress",
                            "monitor-resource-v1",
                            _BIND_SCOPE,
                            MONITOR_INGRESS_OPERATIONS,
                        ),
                    ),
                ),
            )
        },
        policy_version="policy-v1",
        requirements_version="requirements-v1",
    )
    runtime, transport = _runtime(config, registry)
    try:
        with pytest.raises(ServiceRuntimeError, match="CAPABILITY_UNAVAILABLE"):
            runtime.start()
        assert not any(
            request.header.operation == "service.register" for request in transport.requests
        )
    finally:
        runtime.close()
        registry.close()


def test_failure_after_durable_append_remains_unknown_and_is_not_retried(tmp_path):
    config = _service_config(include_upstream=True)
    outbox = DurableOutbox(tmp_path / "unknown-outcome-events.sqlite3")
    component, _target = _build_ingress(config, outbox)
    registry = _registry(component, tmp_path / "unknown-outcome-registry.sqlite3")
    calls: list[int] = []
    append = component.handlers["events.append"]

    def append_then_lose_reply(context, arguments):
        calls.append(1)
        append(context, arguments)
        raise RuntimeError("synthetic reply loss after commit")

    registry.register_handler(component.instance_id, "events.append", append_then_lose_reply)
    runtime, _transport = _runtime(
        config,
        registry,
        requested_operations={"upstream.telemetry": frozenset({"events.append"})},
    )
    try:
        runtime.start()
        with pytest.raises(ServiceCallOutcomeUnknownError):
            runtime.call("upstream.telemetry", "events.append", _EVENT)
        assert calls == [1]
        rows = outbox.read_pending("test-consumer", "monitor:account-a", limit=10)
        assert len(rows) == 1
    finally:
        runtime.close()
        registry.close()
        component.close()
