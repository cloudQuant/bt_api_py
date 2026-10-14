"""Focused SDK delivery tests over the real local durable outbox consumer."""

from __future__ import annotations

import hashlib
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
from bt_api_monitor.durable import (
    DurableOutbox,
    DurableOutboxConsumer,
    OutboxDeliveryError,
    OutboxEvent,
)

from bt_api_py.service_config import parse_service_config
from bt_api_py.service_monitor import (
    ServiceMonitorOutboxSink,
    ServiceMonitorOutboxSinkError,
)
from bt_api_py.service_runtime import ServiceClientRuntime

_PRODUCER = WireIdentity(WireRole.SERVICE, "producer-principal", "monitor-producer")
_PEER = WireIdentity(WireRole.SERVICE, "ingress-principal", "monitor-ingress")
_SCOPE = WireScope(
    "paper:monitor",
    account_ref="account-a",
    strategy_ref="strategy-a",
    run_id="run-a",
    portfolio_ref="portfolio-a",
    group_id="group-a",
    leg_id="leg-a",
)
_SCOPE_REF = "ingress-account-a-v1"
_OUTBOX_SCOPE = "outbox:account-a"
_SERVICE_ID = "monitor-ingress"
_INSTANCE_ID = "monitor-ingress-v1"
_REQUESTED = frozenset({"events.append", "telemetry.append"})


class _RemoteOutbox:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.append_attempts: list[dict[str, Any]] = []

    def append(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.append_attempts.append(arguments)
        source_event_id = arguments["event_id"]
        if source_event_id not in self.events:
            digest = hashlib.sha256(source_event_id.encode("utf-8")).hexdigest()
            self.events[source_event_id] = {
                "event_id": "service-event:" + digest,
                "source_event_id": source_event_id,
                "sequence": len(self.events) + 1,
            }
        return dict(self.events[source_event_id])


class _RegistrationIds:
    def __init__(self) -> None:
        self.value = 0

    def next(self) -> int:
        self.value += 1
        return self.value


class _RuntimeRpcLeaf:
    """Fake only the transport leaf; ServiceClientRuntime remains real."""

    def __init__(
        self,
        remote: _RemoteOutbox,
        registration_ids: _RegistrationIds,
        *,
        service_id: str = _SERVICE_ID,
        instance_ids: tuple[str, ...] = (_INSTANCE_ID,),
    ) -> None:
        self.remote = remote
        self.registration_ids = registration_ids
        self.service_id = service_id
        self.instance_ids = instance_ids
        self.requests = []
        self.closed = False
        self.fail_before_append = False
        self.fail_after_append = False
        self.receipt_transform = None

    def request(self, request, *, timeout):
        assert timeout > 0
        self.requests.append(request)
        operation = request.header.operation
        request_payload = request.payload
        if operation == "service.discover":
            result = {
                "protocol_version": 2,
                "service_id": self.service_id,
                "ready": True,
                "capabilities": [
                    {
                        "instance_id": instance_id,
                        "port": "service.telemetry_ingress",
                        "operations": ["events.append", "telemetry.append"],
                    }
                    for instance_id in self.instance_ids
                ],
            }
        elif operation == "service.register":
            registration_id = self.registration_ids.next()
            result = {
                "registration_id": f"registration-{registration_id}",
                "service_epoch": f"epoch-{registration_id}",
                "policy_version": "policy-v1",
                "requirements_version": "requirements-v1",
                "operations": request_payload["operations"],
                "expires_at": time.time() + 300,
                "lease_remaining_seconds": 300.0,
            }
        elif operation == "service.renew":
            result = {
                "registration_id": request_payload["registration_id"],
                "service_epoch": request_payload["service_epoch"],
                "policy_version": "policy-v1",
                "requirements_version": "requirements-v1",
                "operations": ["events.append", "telemetry.append"],
                "expires_at": time.time() + 300,
                "lease_remaining_seconds": 300.0,
            }
        elif operation == "service.unregister":
            result = {
                "unregistered": True,
                "registration_id": request_payload["registration_id"],
            }
        elif operation == "events.append":
            if self.fail_before_append:
                self.fail_before_append = False
                raise TimeoutError("synthetic transport interruption")
            arguments = dict(request_payload["arguments"])
            result = self.remote.append(arguments)
            if self.receipt_transform is not None:
                result = self.receipt_transform(dict(result))
            if self.fail_after_append:
                self.fail_after_append = False
                raise TimeoutError("synthetic response loss after remote commit")
        else:
            raise AssertionError(f"unexpected runtime operation: {operation}")

        reply = WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=_PEER,
            ),
            {"ok": True, "result": result},
        )
        return reply

    def close(self) -> None:
        self.closed = True


def _config(
    *,
    service_id: str = _SERVICE_ID,
    instance_id: str = _INSTANCE_ID,
    scope_ref: str = _SCOPE_REF,
) -> Any:
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": "producer-service",
            "scope_ref": "producer-root-v1",
            "transport_ref": "listener-v1",
            "registration_policy_ref": "producer-policy-v1",
            "service_requirements_ref": "producer-requirements-v1",
            "components": {
                "monitor": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": "producer-monitor-v1",
                        "bind_ports": ["service.monitor_query"],
                    },
                }
            },
            "clients": {
                "monitor": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "upstream.telemetry",
                                "service_id": service_id,
                                "instance_id": instance_id,
                                "scope_ref": scope_ref,
                                "transport_ref": "upstream-v1",
                            }
                        ]
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "listener-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": "tcp://127.0.0.1:7700"},
                        "security_profile_ref": "listener-security-v1",
                    },
                    "upstream-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7701"},
                        "security_profile_ref": "upstream-security-v1",
                    },
                },
            },
        }
    )


def _runtime(
    remote: _RemoteOutbox,
    *,
    scope: WireScope = _SCOPE,
    service_id: str = _SERVICE_ID,
    instance_id: str = _INSTANCE_ID,
    scope_ref: str = _SCOPE_REF,
    identity: WireIdentity = _PRODUCER,
    requested_operations: frozenset[str] = _REQUESTED,
    registration_ids: _RegistrationIds | None = None,
):
    registrations = registration_ids or _RegistrationIds()
    transports: list[_RuntimeRpcLeaf] = []

    def transport_factory(*_args):
        transport = _RuntimeRpcLeaf(
            remote,
            registrations,
            service_id=service_id,
            instance_ids=(instance_id,),
        )
        transports.append(transport)
        return transport

    runtime = ServiceClientRuntime(
        _config(service_id=service_id, instance_id=instance_id, scope_ref=scope_ref),
        identity=identity,
        scope_resolver=lambda _scope_ref: scope,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=transport_factory,
        run_id=scope.run_id or "runtime-run",
        requested_operations={"upstream.telemetry": requested_operations},
    )
    runtime.start()
    return runtime, transports, registrations


def _event(
    event_id: str = "source-event-1",
    *,
    scope: str = _OUTBOX_SCOPE,
) -> OutboxEvent:
    return OutboxEvent(
        event_id=event_id,
        scope=scope,
        event_type="account.observation",
        data={"equity": "125.50", "labels": ["account", {"source": "test"}]},
        occurred_at=1_800_000_000.25,
    )


def _sink(
    runtime: ServiceClientRuntime, *, scope_ref: str = _SCOPE_REF
) -> ServiceMonitorOutboxSink:
    return ServiceMonitorOutboxSink(
        runtime,
        scope_ref=scope_ref,
        outbox_scope=_OUTBOX_SCOPE,
    )


def _consumer(
    outbox: DurableOutbox,
    sink: ServiceMonitorOutboxSink,
    *,
    scope: str = _OUTBOX_SCOPE,
) -> DurableOutboxConsumer:
    return DurableOutboxConsumer(outbox, sink.consumer_id, scope, deliver=sink)


def test_real_outbox_consumer_calls_explicit_ingress_and_checkpoints(tmp_path):
    remote = _RemoteOutbox()
    runtime, transports, _ = _runtime(remote)
    outbox = DurableOutbox(tmp_path / "monitor-outbox.sqlite")
    try:
        sink = _sink(runtime)
        pending = outbox.append(_event())
        consumer = _consumer(outbox, sink)

        delivered = consumer.consume()

        assert delivered == [pending]
        assert consumer.checkpoint() == pending.sequence
        assert len(remote.events) == 1
        request = next(
            request
            for request in transports[0].requests
            if request.header.operation == "events.append"
        )
        arguments = request.payload["arguments"]
        assert request.header.operation == "events.append"
        assert request.header.identity == _PRODUCER
        assert request.header.scope == _SCOPE
        assert set(arguments) == {
            "schema_version",
            "event_id",
            "event_type",
            "occurred_at",
            "data",
        }
        assert arguments["schema_version"] == 1
        assert arguments["event_id"] == pending.event.event_id
        assert arguments["event_type"] == pending.event.event_type
        assert arguments["occurred_at"] == pending.event.occurred_at
        assert arguments["data"]["labels"][1]["source"] == "test"
    finally:
        runtime.close()


def test_rpc_failure_leaves_event_pending_without_internal_retry(tmp_path):
    remote = _RemoteOutbox()
    runtime, transports, _ = _runtime(remote)
    transports[0].fail_before_append = True
    outbox = DurableOutbox(tmp_path / "monitor-outbox.sqlite")
    outbox.append(_event())
    consumer = _consumer(outbox, _sink(runtime))
    try:
        with pytest.raises(OutboxDeliveryError) as captured:
            consumer.consume()

        assert isinstance(captured.value.__cause__, TimeoutError)
        assert consumer.checkpoint() == 0
        assert (
            len(
                [
                    request
                    for request in transports[0].requests
                    if request.header.operation == "events.append"
                ]
            )
            == 1
        )
        assert remote.append_attempts == []
    finally:
        runtime.close()


def test_runtime_rebind_after_remote_commit_prevents_local_ack(tmp_path):
    remote = _RemoteOutbox()
    runtime, transports, _ = _runtime(remote)
    outbox = DurableOutbox(tmp_path / "monitor-outbox.sqlite")
    outbox.append(_event())
    sink = _sink(runtime)
    consumer = _consumer(outbox, sink)
    original_call = runtime.call

    def call_then_recover(mount, operation, arguments, **kwargs):
        result = original_call(mount, operation, arguments, **kwargs)
        runtime.recover()
        return result

    runtime.call = call_then_recover
    try:
        with pytest.raises(OutboxDeliveryError) as captured:
            consumer.consume()

        assert isinstance(captured.value.__cause__, ServiceMonitorOutboxSinkError)
        assert captured.value.__cause__.code == "INGRESS_RPC_BINDING_CHANGED"
        assert consumer.checkpoint() == 0
        assert len(remote.events) == 1
        assert (
            len(
                [
                    request
                    for request in transports[0].requests
                    if request.header.operation == "events.append"
                ]
            )
            == 1
        )
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "transform",
    [
        lambda receipt: {**receipt, "source_event_id": "wrong-source"},
        lambda receipt: {**receipt, "sequence": True},
        lambda receipt: {**receipt, "sequence": 0},
        lambda receipt: {**receipt, "sequence": 1 << 63},
        lambda receipt: {**receipt, "event_id": "service-event:ABC"},
        lambda receipt: {**receipt, "extra": "unexpected"},
    ],
    ids=(
        "wrong-source",
        "bool-sequence",
        "zero-sequence",
        "overflow-sequence",
        "bad-event-id",
        "extra-key",
    ),
)
def test_invalid_ingress_receipt_does_not_acknowledge(transform, tmp_path):
    remote = _RemoteOutbox()
    runtime, transports, _ = _runtime(remote)
    transports[0].receipt_transform = transform
    outbox = DurableOutbox(tmp_path / "monitor-outbox.sqlite")
    outbox.append(_event())
    consumer = _consumer(outbox, _sink(runtime))
    try:
        with pytest.raises(OutboxDeliveryError) as captured:
            consumer.consume()

        assert isinstance(captured.value.__cause__, ServiceMonitorOutboxSinkError)
        assert captured.value.__cause__.code == "INVALID_INGRESS_RECEIPT"
        assert consumer.checkpoint() == 0
        assert len(remote.events) == 1
    finally:
        runtime.close()


def test_largest_valid_remote_sequence_does_not_replace_local_checkpoint(tmp_path):
    remote = _RemoteOutbox()
    runtime, transports, _ = _runtime(remote)
    transports[0].receipt_transform = lambda receipt: {
        **receipt,
        "sequence": (1 << 63) - 1,
    }
    outbox = DurableOutbox(tmp_path / "monitor-outbox.sqlite")
    pending = outbox.append(_event())
    consumer = _consumer(outbox, _sink(runtime))
    try:
        delivered = consumer.consume()

        assert delivered == [pending]
        assert consumer.checkpoint() == pending.sequence == 1
        assert remote.events["source-event-1"]["sequence"] == 1
    finally:
        runtime.close()


def test_remote_commit_then_local_ack_failure_replays_after_runtime_restart(tmp_path):
    database = tmp_path / "monitor-outbox.sqlite"
    remote = _RemoteOutbox()
    runtime_one, _, registration_ids = _runtime(remote)
    outbox_one = DurableOutbox(database)
    outbox_one.append(_event())
    sink_one = _sink(runtime_one)
    consumer_one = _consumer(outbox_one, sink_one)

    def fail_local_ack(_consumer_id, _scope, _sequence):
        raise OSError("synthetic local checkpoint interruption")

    outbox_one.acknowledge = fail_local_ack
    try:
        with pytest.raises(OutboxDeliveryError) as captured:
            consumer_one.consume()
        assert isinstance(captured.value.__cause__, OSError)
        assert consumer_one.checkpoint() == 0
        assert len(remote.events) == 1
        runtime_one.close()

        restarted_scope = replace(_SCOPE, run_id="run-after-restart")
        runtime_two, transports_two, _ = _runtime(
            remote,
            scope=restarted_scope,
            registration_ids=registration_ids,
        )
        outbox_two = DurableOutbox(database)
        sink_two = _sink(runtime_two)
        consumer_two = _consumer(outbox_two, sink_two)
        try:
            assert sink_two.consumer_id == sink_one.consumer_id
            delivered = consumer_two.consume()
            assert [item.event.event_id for item in delivered] == ["source-event-1"]
            assert consumer_two.checkpoint() == 1
            assert len(remote.events) == 1
            assert len(remote.append_attempts) == 2
            assert transports_two[0].requests[-1].header.operation == "events.append"
        finally:
            runtime_two.close()
    finally:
        if runtime_one.state != "closed":
            runtime_one.close()
        del outbox_one.acknowledge


def test_scope_and_service_identity_must_match_before_any_rpc():
    remote = _RemoteOutbox()
    runtime, transports, _ = _runtime(remote)
    try:
        with pytest.raises(ServiceMonitorOutboxSinkError, match="INGRESS_BINDING_UNAVAILABLE"):
            _sink(runtime, scope_ref="different-ingress-scope")

        runtime.identity = WireIdentity(
            WireRole.APPLICATION_CLIENT,
            "producer-principal",
            "monitor-producer",
        )
        with pytest.raises(ServiceMonitorOutboxSinkError, match="INGRESS_BINDING_UNAVAILABLE"):
            _sink(runtime)
        assert not any(
            request.header.operation == "events.append" for request in transports[0].requests
        )
    finally:
        runtime.identity = _PRODUCER
        runtime.close()


def test_missing_events_append_grant_is_rejected_even_when_telemetry_is_granted():
    remote = _RemoteOutbox()
    runtime, transports, _ = _runtime(remote, requested_operations=frozenset({"telemetry.append"}))
    try:
        with pytest.raises(ServiceMonitorOutboxSinkError, match="INGRESS_BINDING_UNAVAILABLE"):
            _sink(runtime)
        assert transports[0].requests[-1].header.operation == "service.register"
    finally:
        runtime.close()


def test_local_outbox_scope_mismatch_does_not_call_remote_or_ack(tmp_path):
    remote = _RemoteOutbox()
    runtime, transports, _ = _runtime(remote)
    outbox = DurableOutbox(tmp_path / "monitor-outbox.sqlite")
    outbox.append(_event(scope="outbox:other-account"))
    consumer = _consumer(outbox, _sink(runtime), scope="outbox:other-account")
    try:
        with pytest.raises(OutboxDeliveryError) as captured:
            consumer.consume()
        assert isinstance(captured.value.__cause__, ServiceMonitorOutboxSinkError)
        assert captured.value.__cause__.code == "OUTBOX_SCOPE_MISMATCH"
        assert consumer.checkpoint() == 0
        assert not any(
            request.header.operation == "events.append" for request in transports[0].requests
        )
    finally:
        runtime.close()


def test_consumer_identity_is_stable_across_run_id_but_binds_destination_and_scope():
    remote = _RemoteOutbox()
    runtime_one, _, ids = _runtime(remote)
    runtime_two, _, _ = _runtime(
        remote, scope=replace(_SCOPE, run_id="next-run"), registration_ids=ids
    )
    runtime_other_target, _, _ = _runtime(
        remote,
        service_id="monitor-ingress-secondary",
        registration_ids=ids,
    )
    runtime_other_scope, _, _ = _runtime(
        remote,
        scope=replace(_SCOPE, leg_id="leg-b"),
        registration_ids=ids,
    )
    try:
        first = _sink(runtime_one)
        same = _sink(runtime_two)
        other_target = _sink(runtime_other_target)
        other_scope = _sink(runtime_other_scope)
        assert first.consumer_id == same.consumer_id
        assert first.consumer_id != other_target.consumer_id
        assert first.consumer_id != other_scope.consumer_id
        assert runtime_one.bindings[0].registration_id != runtime_two.bindings[0].registration_id
        assert runtime_one.bindings[0].service_epoch != runtime_two.bindings[0].service_epoch
    finally:
        runtime_one.close()
        runtime_two.close()
        runtime_other_target.close()
        runtime_other_scope.close()


def test_multiple_destinations_are_selected_by_exact_scope_ref():
    remote = _RemoteOutbox()
    scope_b = "ingress-account-b-v1"
    runtime, transports, _ = _runtime_with_two_targets(remote, scope_b)
    try:
        sink_a = _sink(runtime)
        sink_b = ServiceMonitorOutboxSink(
            runtime,
            scope_ref=scope_b,
            outbox_scope="outbox:account-b",
        )
        assert sink_a.consumer_id != sink_b.consumer_id
        assert not any(
            request.header.operation == "events.append" for request in transports[0].requests
        )
    finally:
        runtime.close()


def test_duplicate_active_append_route_for_same_scope_is_ambiguous():
    remote = _RemoteOutbox()
    runtime, transports, _ = _runtime_with_duplicate_target(remote)
    try:
        with pytest.raises(ServiceMonitorOutboxSinkError, match="INGRESS_BINDING_UNAVAILABLE"):
            _sink(runtime)
        assert not any(
            request.header.operation == "events.append" for request in transports[0].requests
        )
    finally:
        runtime.close()


def _runtime_with_two_targets(remote: _RemoteOutbox, second_scope_ref: str):
    raw = _config().to_public_dict()
    raw["clients"]["monitor"]["settings"]["bindings"].append(
        {
            "mount": "upstream.telemetry",
            "service_id": _SERVICE_ID,
            "instance_id": _INSTANCE_ID,
            "scope_ref": second_scope_ref,
            "transport_ref": "upstream-v1",
        }
    )
    config = parse_service_config(raw)
    ids = _RegistrationIds()
    transport = _RuntimeRpcLeaf(
        remote,
        ids,
        service_id=_SERVICE_ID,
        instance_ids=(_INSTANCE_ID,),
    )
    runtime = ServiceClientRuntime(
        config,
        identity=_PRODUCER,
        scope_resolver=lambda _ref: _SCOPE,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=lambda *_: transport,
        requested_operations={"upstream.telemetry": _REQUESTED},
    )
    runtime.start()
    return runtime, [transport], ids


def _runtime_with_duplicate_target(remote: _RemoteOutbox):
    raw = _config().to_public_dict()
    raw["clients"]["monitor"]["settings"]["bindings"].append(
        {
            "mount": "upstream.telemetry",
            "service_id": _SERVICE_ID,
            "instance_id": _INSTANCE_ID + "-second",
            "scope_ref": _SCOPE_REF,
            "transport_ref": "upstream-v1",
        }
    )
    config = parse_service_config(raw)
    ids = _RegistrationIds()
    transport = _RuntimeRpcLeaf(
        remote,
        ids,
        instance_ids=(_INSTANCE_ID, _INSTANCE_ID + "-second"),
    )
    runtime = ServiceClientRuntime(
        config,
        identity=_PRODUCER,
        scope_resolver=lambda _ref: _SCOPE,
        peer_resolver=lambda _service_id: _PEER,
        transport_factory=lambda *_: transport,
        requested_operations={"upstream.telemetry": _REQUESTED},
    )
    runtime.start()
    return runtime, [transport], ids
