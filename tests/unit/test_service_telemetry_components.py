"""Fixed configuration and handler wiring for ordinary Monitor telemetry."""

from __future__ import annotations

from dataclasses import replace

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_base.telemetry_protocol import TelemetrySample

from bt_api_py.service_components import (
    ComponentResources,
    ServiceComponentError,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_registry import ServiceCallContext, ServiceResultTooLargeError
from bt_api_py.service_runtime import ServiceClientRuntime
from bt_api_py.service_telemetry import ServiceTelemetryEmitter
from tests.unit.test_service_monitor_ingress import _service_config

_SCOPE = WireScope("paper:demo", strategy_ref="strategy-a")


def _prepared(options=None, ports=("service.telemetry_ingress", "service.monitor_query")):
    config = _service_config(
        bind_ports=ports,
        options=options or {"role": "telemetry_ingress"},
    )
    return validate_components(component_specs(config.components))[0]


def test_telemetry_role_declares_only_ordinary_operations_and_normalized_limits():
    prepared = _prepared()
    assert prepared.operations_by_port == {
        "service.telemetry_ingress": frozenset({"telemetry.append"}),
        "service.monitor_query": frozenset({"telemetry.snapshot"}),
    }
    assert prepared.options == {
        "role": "telemetry_ingress",
        "max_streams": 256,
        "max_bytes": 1024 * 1024,
        "heartbeat_timeout_seconds": 30,
        "retention_seconds": 300,
    }


def test_fixed_telemetry_handlers_forward_trusted_identity_scope_and_reply_budget():
    component = build_component(
        _prepared(),
        scope=_SCOPE,
        resource_provider=lambda *_: ComponentResources.borrowed(
            {"targets": frozenset({_SCOPE})}, ready=lambda: True
        ),
    )
    producer = WireIdentity(WireRole.STRATEGY_CLIENT, "producer-a", "strategy-a")
    run_scope = WireScope("paper:demo", strategy_ref="strategy-a", run_id="run-a")
    append_context = ServiceCallContext(producer, run_scope, "registration-a", "producer-a")
    sample = TelemetrySample("stream-a", 1, "heartbeat", 1_800_000_000.0, {}, 0, 0)
    try:
        assert component.handlers["telemetry.append"](append_context, sample.to_dict()) == {
            "stream_id": "stream-a",
            "sequence": 1,
            "accepted": True,
        }
        application = WireIdentity(WireRole.APPLICATION_CLIENT, "dashboard", "dashboard")
        query_context = ServiceCallContext(application, _SCOPE, "query-a", "dashboard")
        result = component.handlers["telemetry.snapshot"](query_context, {})
        assert result["streams"][0]["producer"]["principal_ref"] == "producer-a"
        assert result["streams"][0]["scope"]["run_id"] == "run-a"
        limited_context = ServiceCallContext(
            application,
            _SCOPE,
            "query-a",
            "dashboard",
            result_fits=lambda _: False,
        )
        with pytest.raises(ServiceResultTooLargeError):
            component.handlers["telemetry.snapshot"](limited_context, {})
    finally:
        component.close()


@pytest.mark.parametrize(
    "limits",
    [
        {"max_streams": 10_001},
        {"max_bytes": 64 * 1024 * 1024 + 1},
        {"heartbeat_timeout_seconds": 31, "retention_seconds": 30},
    ],
)
def test_invalid_server_limits_fail_before_resource_resolution(limits):
    with pytest.raises(ServiceComponentError, match="COMPONENT_OPTIONS_INVALID"):
        _prepared({"role": "telemetry_ingress", **limits})


def test_owned_resources_are_closed_if_telemetry_targets_are_invalid():
    closed = []
    resources = ComponentResources.owned(
        {"targets": frozenset()}, ready=lambda: True, close=lambda: closed.append(True)
    )
    with pytest.raises(ValueError, match="TARGETS_REQUIRED"):
        build_component(_prepared(), scope=_SCOPE, resource_provider=lambda *_: resources)
    assert closed == [True]


def test_failed_telemetry_flush_leaves_unrelated_required_read_binding_usable():
    from tests.unit.test_service_telemetry import _CLIENT, _PEER, _SCOPE_REF, _config, _RpcLeaf

    raw = _config().to_public_dict()
    binding = dict(raw["clients"]["monitor"]["settings"]["bindings"][0])
    binding.update(
        mount="risk.read_model",
        instance_id="risk-read",
        scope_ref="risk-account-a-v1",
        account_ref="account-a",
        required=True,
    )
    raw["clients"]["risk"] = {"config_schema_version": 1, "settings": {"bindings": [binding]}}

    class ReadAndTelemetryLeaf(_RpcLeaf):
        def request(self, request, *, timeout):
            if request.header.operation == "risk.snapshot":
                return WireEnvelope(
                    replace(
                        request.header,
                        kind=WireMessageKind.REPLY,
                        message_id="reply-" + request.header.message_id,
                        correlation_id=request.header.message_id,
                        identity=_PEER,
                    ),
                    {"ok": True, "result": {"available": True}},
                )
            reply = super().request(request, timeout=timeout)
            result = dict(reply.payload["result"])
            if request.header.operation == "service.discover":
                result["capabilities"] = [
                    *result["capabilities"],
                    {
                        "instance_id": "risk-read",
                        "port": "service.risk_query",
                        "operations": ["risk.snapshot"],
                    },
                ]
            elif request.header.operation == "service.register":
                result["registration_id"] += "-" + request.payload["instance_id"]
            return WireEnvelope(reply.header, {"ok": True, "result": result})

    leaf = ReadAndTelemetryLeaf()
    runtime = ServiceClientRuntime(
        parse_client_config(raw),
        identity=_CLIENT,
        scope_resolver=lambda _: WireScope("paper:telemetry", account_ref="account-a"),
        peer_resolver=lambda _: _PEER,
        transport_factory=lambda *_: leaf,
        run_id="run-a",
    )
    runtime.start()
    required = next(item for item in runtime.bindings if item.required)
    emitter = ServiceTelemetryEmitter(runtime, scope_ref=_SCOPE_REF)
    try:
        assert emitter.enqueue_heartbeat()
        leaf.fail_before_append = True
        flushed = emitter.flush()
        assert (flushed.attempted, flushed.delivered, flushed.dropped) == (1, 0, 1)
        assert emitter.stats.state == "degraded"
        assert runtime.state == "registered" and runtime.healthy
        assert required in runtime.bindings
        assert runtime.call("risk.read_model", "risk.snapshot", {}, account_ref="account-a") == {
            "available": True
        }
    finally:
        emitter.close()
        runtime.close()
