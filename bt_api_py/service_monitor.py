"""Synchronous SDK delivery from the monitor's durable local outbox.

The outbox consumer and service runtime are owned by the caller. This sink
borrows an already registered runtime and performs one explicit ingress RPC
per event; it has no worker, queue, retry, or runtime lifecycle of its own.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import fields

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_runtime import (
    RegisteredBinding,
    ServiceClientRuntime,
)

_MOUNT = "upstream.telemetry"
_OPERATION = "events.append"
_MAX_REFERENCE_BYTES = 128
_MAX_OUTBOX_SCOPE_BYTES = 256
_SEQUENCE_LIMIT = 1 << 63
_EVENT_ID_RE = re.compile(r"service-event:[0-9a-f]{64}")


class ServiceMonitorOutboxSinkError(RuntimeError):
    """Finite, local diagnostics for invalid sink configuration or receipts."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ServiceMonitorOutboxSink:
    """Deliver durable monitor facts over one explicitly granted service RPC.

    The caller must keep the borrowed runtime maintained on its owner thread.
    After an unknown call outcome, the caller should close/recover that runtime
    according to its lifecycle policy and construct a fresh sink. The durable
    consumer will replay the unacknowledged source event; ingress de-duplicates
    it by producer, scope, destination outbox, and source event ID.
    """

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        *,
        scope_ref: str,
        outbox_scope: str,
    ) -> None:
        if not isinstance(runtime, ServiceClientRuntime):
            raise ServiceMonitorOutboxSinkError("INVALID_RUNTIME")
        if not _valid_text(scope_ref, _MAX_REFERENCE_BYTES):
            raise ServiceMonitorOutboxSinkError("INVALID_SCOPE_REF")
        if not _valid_text(outbox_scope, _MAX_OUTBOX_SCOPE_BYTES):
            raise ServiceMonitorOutboxSinkError("INVALID_OUTBOX_SCOPE")

        try:
            signature, binding, identity = _current_binding(runtime, scope_ref)
        except Exception:
            raise ServiceMonitorOutboxSinkError("INGRESS_BINDING_UNAVAILABLE") from None

        self._runtime = runtime
        self._scope_ref = scope_ref
        self._outbox_scope = outbox_scope
        self._owner_thread_id = threading.get_ident()
        self._identity = identity
        self._stable_binding = _stable_binding(binding, identity, outbox_scope)
        self._rpc_signature = signature
        self._consumer_id = (
            "sdk-monitor-outbox-v1:"
            + hashlib.sha256(_canonical_json(self._stable_binding)).hexdigest()
        )

    @property
    def consumer_id(self) -> str:
        """Return the stable local checkpoint identity for this destination."""

        return self._consumer_id

    @property
    def outbox_scope(self) -> str:
        """Return the sole local outbox scope accepted by this sink."""

        return self._outbox_scope

    def __call__(self, item: object) -> None:
        """Append one event remotely or raise so the local consumer does not ACK."""

        self._assert_owner_thread()
        # Keep the optional monitor dependency out of the SDK's import path.
        try:
            from bt_api_monitor.durable import OutboxEvent, SequencedOutboxEvent
        except ImportError:
            raise ServiceMonitorOutboxSinkError("MONITOR_OUTBOX_UNAVAILABLE") from None

        if (
            type(item) is not SequencedOutboxEvent
            or type(item.sequence) is not int
            or not 1 <= item.sequence < _SEQUENCE_LIMIT
            or type(item.event) is not OutboxEvent
        ):
            raise ServiceMonitorOutboxSinkError("INVALID_OUTBOX_EVENT")
        event = item.event
        if event.scope != self._outbox_scope:
            raise ServiceMonitorOutboxSinkError("OUTBOX_SCOPE_MISMATCH")
        if (
            type(event.event_id) is not str
            or not _valid_text(event.event_id, 256)
            or type(event.event_type) is not str
            or not _valid_text(event.event_type, 128)
            or type(event.occurred_at) not in (int, float)
        ):
            raise ServiceMonitorOutboxSinkError("INVALID_OUTBOX_EVENT")

        self._validate_current_binding()
        arguments = {
            "schema_version": 1,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "data": dict(event.data),
        }
        # Runtime exceptions, including unknown outcomes, propagate unchanged.
        # DurableOutboxConsumer consequently leaves this sequence unacknowledged.
        receipt = self._runtime.call(
            _MOUNT,
            _OPERATION,
            arguments,
            scope_ref=self._scope_ref,
        )
        self._validate_current_binding()
        _validate_receipt(receipt, source_event_id=event.event_id)
        return None

    def _validate_current_binding(self) -> None:
        self._assert_owner_thread()
        try:
            signature, binding, identity = _current_binding(self._runtime, self._scope_ref)
            stable = _stable_binding(binding, identity, self._outbox_scope)
        except Exception:
            raise ServiceMonitorOutboxSinkError("INGRESS_BINDING_UNAVAILABLE") from None
        if identity != self._identity or stable != self._stable_binding:
            raise ServiceMonitorOutboxSinkError("INGRESS_BINDING_CHANGED")
        if signature != self._rpc_signature:
            raise ServiceMonitorOutboxSinkError("INGRESS_RPC_BINDING_CHANGED")

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceMonitorOutboxSinkError("WRONG_OWNER_THREAD")


def _current_binding(
    runtime: ServiceClientRuntime, scope_ref: str
) -> tuple[tuple[object, ...], RegisteredBinding, WireIdentity]:
    if runtime.state != "registered" or runtime.healthy is not True:
        raise ValueError("runtime is not registered and healthy")
    identity = runtime.identity
    if type(identity) is not WireIdentity or identity.role is not WireRole.SERVICE:
        raise ValueError("producer must have a trusted service identity")
    bindings = runtime.bindings
    if type(bindings) is not tuple:
        raise ValueError("runtime binding list is invalid")
    candidates = [
        binding
        for binding in bindings
        if type(binding) is RegisteredBinding
        and binding.active is True
        and binding.mount == _MOUNT
        and binding.scope_ref == scope_ref
        and type(binding.operations) is frozenset
        and _OPERATION in binding.operations
    ]
    if len(candidates) != 1:
        raise ValueError("one active durable ingress grant is required")
    binding = candidates[0]
    if type(binding.scope) is not WireScope:
        raise ValueError("the configured durable ingress target does not match")
    signature = _rpc_binding_signature(binding, identity)
    return signature, binding, identity


def _rpc_binding_signature(
    binding: RegisteredBinding, identity: WireIdentity
) -> tuple[object, ...]:
    return (
        binding.mount,
        binding.service_id,
        binding.instance_id,
        binding.scope_ref,
        binding.scope,
        binding.registration_id,
        binding.service_epoch,
        binding.operations,
        binding.transport_ref,
        binding.active,
        identity.role,
        identity.principal_ref,
        identity.actor_ref,
        identity.delegate_ref,
    )


def _stable_binding(
    binding: RegisteredBinding,
    identity: WireIdentity,
    outbox_scope: str,
) -> dict[str, object]:
    scope = binding.scope
    if type(scope) is not WireScope:
        raise ValueError("wire scope is invalid")
    scope_values = {
        item.name: getattr(scope, item.name) for item in fields(WireScope) if item.name != "run_id"
    }
    return {
        "schema": "sdk.service_monitor_outbox.binding.v1",
        "mount": binding.mount,
        "service_id": binding.service_id,
        "instance_id": binding.instance_id,
        "scope_ref": binding.scope_ref,
        "scope": scope_values,
        "identity": {
            "role": identity.role.value,
            "principal_ref": identity.principal_ref,
            "actor_ref": identity.actor_ref,
            "delegate_ref": identity.delegate_ref,
        },
        "outbox_scope": outbox_scope,
    }


def _validate_receipt(reply: object, *, source_event_id: str) -> None:
    if not isinstance(reply, Mapping) or set(reply) != {
        "event_id",
        "source_event_id",
        "sequence",
    }:
        raise ServiceMonitorOutboxSinkError("INVALID_INGRESS_RECEIPT")
    event_id = reply.get("event_id")
    echoed_source_id = reply.get("source_event_id")
    sequence = reply.get("sequence")
    if (
        type(event_id) is not str
        or _EVENT_ID_RE.fullmatch(event_id) is None
        or type(echoed_source_id) is not str
        or echoed_source_id != source_event_id
        or type(sequence) is not int
        or not 1 <= sequence < _SEQUENCE_LIMIT
    ):
        raise ServiceMonitorOutboxSinkError("INVALID_INGRESS_RECEIPT")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8", errors="strict")


def _valid_text(value: object, max_bytes: int) -> bool:
    if type(value) is not str or not value or value != value.strip():
        return False
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return len(encoded) <= max_bytes and not any(
        ord(character) < 32 or ord(character) == 127 for character in value
    )


__all__ = ["ServiceMonitorOutboxSink", "ServiceMonitorOutboxSinkError"]
