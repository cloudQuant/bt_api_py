"""Focused queue and delivery behavior for ordinary SDK telemetry."""

from __future__ import annotations

import math
import time
from dataclasses import replace
from typing import Any

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_base.telemetry_protocol import parse_telemetry_sample

from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import ServiceClientRuntime
from bt_api_py.service_telemetry import (
    ServiceTelemetryEmitter,
    ServiceTelemetryEmitterError,
)

_CLIENT = WireIdentity(WireRole.STRATEGY_CLIENT, "strategy-principal", "strategy-a")
_PEER = WireIdentity(WireRole.SERVICE, "monitor-principal", "telemetry-service")
_SCOPE = WireScope("paper:telemetry", strategy_ref="strategy-a")
_SERVICE_ID = "telemetry-service"
_INSTANCE_ID = "telemetry-v1"
_SCOPE_REF = "strategy-a-v1"
_PROFILE_REF = "telemetry-zmq"


class _RpcLeaf:
    """Fake the transport leaf while using the actual client runtime."""

    def __init__(self) -> None:
        self.requests = []
        self.telemetry_arguments: list[dict[str, Any]] = []
        self.closed = False
        self.fail_before_append = False
        self.fail_after_append = False
        self.receipt_override: Any = None
        self.after_append = None

    def request(self, request, *, timeout):
        assert timeout > 0
        self.requests.append(request)
        operation = request.header.operation
        payload = request.payload
        if operation == "service.discover":
            result = {
                "protocol_version": 2,
                "service_id": _SERVICE_ID,
                "ready": True,
                "capabilities": [
                    {
                        "instance_id": _INSTANCE_ID,
                        "port": "service.telemetry_ingress",
                        "operations": ["telemetry.append"],
                    }
                ],
            }
        elif operation == "service.register":
            result = {
                "registration_id": "registration-1",
                "service_epoch": "epoch-1",
                "policy_version": "policy-v1",
                "requirements_version": "requirements-v1",
                "operations": payload["operations"],
                "expires_at": time.time() + 300,
                "lease_remaining_seconds": 300.0,
            }
        elif operation == "service.renew":
            result = {
                "registration_id": payload["registration_id"],
                "service_epoch": payload["service_epoch"],
                "policy_version": "policy-v1",
                "requirements_version": "requirements-v1",
                "operations": ["telemetry.append"],
                "expires_at": time.time() + 300,
                "lease_remaining_seconds": 300.0,
            }
        elif operation == "service.unregister":
            result = {
                "unregistered": True,
                "registration_id": payload["registration_id"],
            }
        elif operation == "telemetry.append":
            arguments = dict(payload["arguments"])
            if self.fail_before_append:
                self.fail_before_append = False
                raise TimeoutError("synthetic request failure")
            self.telemetry_arguments.append(arguments)
            if self.after_append is not None:
                self.after_append()
            if self.fail_after_append:
                self.fail_after_append = False
                raise TimeoutError("synthetic response loss")
            result = self.receipt_override
            if result is None:
                result = {
                    "stream_id": arguments["stream_id"],
                    "sequence": arguments["sequence"],
                    "accepted": True,
                }
        else:
            raise AssertionError(f"unexpected SDK operation: {operation}")

        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=_PEER,
            ),
            {"ok": True, "result": result},
        )

    def close(self) -> None:
        self.closed = True


def _config(*, required: bool = False, options: dict[str, Any] | None = None):
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": "strategy-a"},
            "clients": {
                "monitor": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "runtime.telemetry",
                                "service_id": _SERVICE_ID,
                                "instance_id": _INSTANCE_ID,
                                "scope_ref": _SCOPE_REF,
                                "transport_ref": _PROFILE_REF,
                                "required": required,
                            }
                        ],
                        **({"options": options} if options is not None else {}),
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _PROFILE_REF: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7781"},
                        "security_profile_ref": "telemetry-keys",
                    }
                },
            },
        }
    )


def _runtime(*, available: bool = True, required: bool = False):
    config = _config(required=required)
    leaves: list[_RpcLeaf] = []

    def transport_factory(_profile, _peer):
        if not available:
            raise ConnectionError("optional telemetry service unavailable")
        leaf = _RpcLeaf()
        leaves.append(leaf)
        return leaf

    runtime = ServiceClientRuntime(
        config,
        identity=_CLIENT,
        scope_resolver=lambda _ref: _SCOPE,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=transport_factory,
        run_id="run-a",
    )
    runtime.start()
    return runtime, leaves


def _emitter(runtime, **kwargs):
    return ServiceTelemetryEmitter(
        runtime,
        scope_ref=_SCOPE_REF,
        clock=lambda: 1_800_000_000.25,
        **kwargs,
    )


def test_enqueue_is_io_free_then_flush_sends_exact_telemetry_dto():
    runtime, leaves = _runtime()
    emitter = _emitter(runtime)
    try:
        request_count = len(leaves[0].requests)
        assert emitter.enqueue_heartbeat() is True
        assert emitter.enqueue_metrics({"equity": 125.5, "positions.count": 3}) is True
        assert len(leaves[0].requests) == request_count
        assert emitter.stats.queued_events == 2

        result = emitter.flush(max_events=2)

        assert result.attempted == 2
        assert result.delivered == 2
        assert result.dropped == 0
        assert result.ready is True
        assert emitter.stats.queued_events == 0
        assert emitter.stats.delivered_total == 2
        heartbeat, metrics = [
            parse_telemetry_sample(arguments) for arguments in leaves[0].telemetry_arguments
        ]
        assert heartbeat.kind == "heartbeat" and dict(heartbeat.metrics) == {}
        assert metrics.kind == "metrics"
        assert dict(metrics.metrics) == {"equity": 125.5, "positions.count": 3}
        assert heartbeat.sequence == 1 and metrics.sequence == 2
        assert heartbeat.stream_id == emitter.stream_id == metrics.stream_id
    finally:
        emitter.close()
        runtime.close()


def test_overflow_drops_newest_and_next_sample_reports_queue_drop_count():
    runtime, leaves = _runtime()
    emitter = _emitter(runtime, max_events=1)
    try:
        assert emitter.enqueue_heartbeat() is True
        assert emitter.enqueue_heartbeat() is False
        assert emitter.stats.dropped_queue_total == 1
        assert emitter.stats.queued_events == 1

        result = emitter.flush()

        assert result.delivered == 1 and result.dropped == 0
        sent = parse_telemetry_sample(leaves[0].telemetry_arguments[0])
        assert sent.dropped_queue_total == 1
    finally:
        emitter.close()
        runtime.close()


def test_byte_capacity_is_bounded_without_blocking_enqueue():
    runtime, leaves = _runtime()
    emitter = _emitter(runtime, max_bytes=1)
    try:
        before = len(leaves[0].requests)
        assert emitter.enqueue_heartbeat() is False
        assert emitter.stats.queued_events == 0
        assert emitter.stats.queued_bytes == 0
        assert emitter.stats.dropped_queue_total == 1
        assert len(leaves[0].requests) == before
    finally:
        emitter.close()
        runtime.close()


def test_unknown_delivery_is_dropped_once_and_later_sample_reports_it():
    runtime, leaves = _runtime()
    emitter = _emitter(runtime)
    leaf = leaves[0]
    try:
        assert emitter.enqueue_metrics({"first": 1})
        assert emitter.enqueue_metrics({"second": 2})
        leaf.fail_after_append = True

        first = emitter.flush(max_events=2)

        assert first.attempted == 1
        assert first.delivered == 0
        assert first.dropped == 1
        assert first.ready is False
        assert emitter.stats.queued_events == 1
        assert emitter.stats.dropped_delivery_total == 1
        assert len(leaf.telemetry_arguments) == 1

        second = emitter.flush()

        assert second.attempted == second.delivered == 1
        assert second.dropped == 0 and second.ready is True
        sent = parse_telemetry_sample(leaf.telemetry_arguments[1])
        assert sent.metrics == {"second": 2}
        assert sent.dropped_delivery_total == 1
        assert [item["sequence"] for item in leaf.telemetry_arguments] == [1, 2]
    finally:
        emitter.close()
        runtime.close()


@pytest.mark.parametrize(
    "receipt",
    [
        {"stream_id": "wrong", "sequence": 1, "accepted": True},
        {"stream_id": "", "sequence": 1, "accepted": True},
        {"stream_id": "stream", "sequence": True, "accepted": True},
        {"stream_id": "stream", "sequence": 1, "accepted": False},
        {"stream_id": "stream", "sequence": 1, "accepted": True, "extra": 1},
    ],
)
def test_malformed_receipt_drops_sample_and_stops_flush(receipt):
    runtime, leaves = _runtime()
    emitter = _emitter(runtime)
    leaf = leaves[0]
    leaf.receipt_override = receipt
    try:
        assert emitter.enqueue_heartbeat()
        assert emitter.enqueue_heartbeat()

        result = emitter.flush(max_events=2)

        assert result.attempted == 1 and result.delivered == 0
        assert result.dropped == 1 and result.ready is False
        assert emitter.stats.queued_events == 1
        assert emitter.stats.last_error_code == "INVALID_RECEIPT"
        assert len(leaf.telemetry_arguments) == 1
    finally:
        emitter.close()
        runtime.close()


def test_invalid_metric_input_fails_before_queue_or_rpc():
    runtime, leaves = _runtime()
    emitter = _emitter(runtime)
    try:
        request_count = len(leaves[0].requests)
        for invalid in (
            {},
            {"equity": True},
            {"nested": {"value": 1}},
            {"api_token": 1},
            {"nan": math.nan},
        ):
            with pytest.raises(ServiceTelemetryEmitterError, match="INVALID_SAMPLE"):
                emitter.enqueue_metrics(invalid)
        assert emitter.stats.queued_events == 0
        assert len(leaves[0].requests) == request_count
    finally:
        emitter.close()
        runtime.close()


def test_optional_missing_route_keeps_enqueue_available_and_flush_drops_locally():
    runtime, leaves = _runtime(available=False)
    emitter = _emitter(runtime)
    try:
        assert runtime.state == "registered"
        assert runtime.bindings == ()
        assert emitter.stats.state == "degraded"
        assert emitter.enqueue_heartbeat() is True

        result = emitter.flush()

        assert result.attempted == 1 and result.delivered == 0
        assert result.dropped == 1 and result.ready is False
        assert emitter.stats.dropped_delivery_total == 1
        assert leaves == []
    finally:
        emitter.close()
        runtime.close()


def test_required_telemetry_binding_is_rejected_and_from_config_reads_limits():
    runtime, leaves = _runtime(required=True)
    try:
        with pytest.raises(
            ServiceTelemetryEmitterError, match="TELEMETRY_BINDING_MUST_BE_OPTIONAL"
        ):
            _emitter(runtime)
    finally:
        runtime.close()

    configured = _config(
        options={"telemetry_queue_max_events": 1, "telemetry_queue_max_bytes": 4096}
    )
    runtime, leaves = _runtime()
    emitter = ServiceTelemetryEmitter.from_config(
        runtime,
        configured,
        scope_ref=_SCOPE_REF,
        clock=lambda: 1_800_000_000.25,
    )
    try:
        assert emitter.enqueue_heartbeat() is True
        assert emitter.enqueue_heartbeat() is False
        assert emitter.stats.dropped_queue_total == 1
    finally:
        emitter.close()
        runtime.close()


def test_emitter_close_discards_only_its_queue_and_borrows_runtime():
    runtime, leaves = _runtime()
    emitter = _emitter(runtime)
    leaf = leaves[0]
    bindings_before = runtime.bindings
    try:
        assert emitter.enqueue_heartbeat()
        emitter.close()
        emitter.close()
        assert runtime.state == "registered"
        assert runtime.bindings == bindings_before
        assert leaf.closed is False
        assert emitter.stats.state == "closed"
        assert emitter.stats.queued_events == 0
        assert emitter.stats.dropped_delivery_total == 1
        with pytest.raises(ServiceTelemetryEmitterError, match="CLOSED"):
            emitter.enqueue_heartbeat()
    finally:
        runtime.close()


def test_identity_or_full_scope_change_drops_without_sending():
    for change in ("identity", "scope"):
        runtime, leaves = _runtime()
        emitter = _emitter(runtime)
        try:
            assert emitter.enqueue_heartbeat()
            if change == "identity":
                runtime.identity = WireIdentity(
                    WireRole.STRATEGY_CLIENT, "other-principal", "strategy-a"
                )
            else:
                binding = runtime._bindings[0]
                runtime._bindings[0] = replace(
                    binding,
                    scope=replace(binding.scope, execution_domain_ref="live:telemetry"),
                )

            result = emitter.flush()

            assert result.attempted == 1 and result.delivered == 0
            assert result.dropped == 1 and result.ready is False
            assert leaves[0].telemetry_arguments == []
            assert emitter.stats.last_error_code in {"IDENTITY_CHANGED", "BINDING_CHANGED"}
        finally:
            emitter.close()
            runtime.close()


def test_full_scope_change_after_remote_acceptance_is_not_retried():
    runtime, leaves = _runtime()
    emitter = _emitter(runtime)
    leaf = leaves[0]
    binding = runtime.bindings[0]

    def change_scope_after_remote_append():
        runtime._bindings[0] = replace(
            runtime._bindings[0],
            scope=replace(binding.scope, execution_domain_ref="live:telemetry"),
        )

    leaf.after_append = change_scope_after_remote_append
    try:
        assert emitter.enqueue_heartbeat()

        result = emitter.flush()

        assert result.attempted == 1 and result.delivered == 0
        assert result.dropped == 1 and result.ready is False
        assert len(leaf.telemetry_arguments) == 1
        assert emitter.stats.last_error_code == "BINDING_CHANGED"
        assert emitter.stats.dropped_delivery_total == 1
    finally:
        emitter.close()
        runtime.close()
