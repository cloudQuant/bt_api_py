"""Explicit adapter for local-parent observations to durable Monitor ingress.

The binding is supplied by trusted composition; it does not establish provider
identity or authorize the source reader. Neither this adapter nor its sink owns a
thread, network timeout, runtime, source, or checkpoint. A local RECEIVED event
does not assert Gateway or venue acceptance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from bt_api_base.service_protocol import WireScope

from bt_api_py.service_local_parent_monitor import (
    LocalParentMonitorAck, LocalParentMonitorEvent, LocalParentMonitorRelay,
    _reader_is_supported,
)
from bt_api_py.service_monitor import ServiceMonitorOutboxSink, _current_binding

LOCAL_PARENT_EVENT_TYPE = 'execution.local_parent_received.v1'
LOCAL_PARENT_OUTBOX_SCOPE = 'sdk.execution.local_parent_observations.v1'
_EVENT_ID = re.compile(r'service-event:[0-9a-f]{64}\Z')
_FINGERPRINT = re.compile(r'[0-9a-f]{64}\Z')


class LocalParentMonitorSinkError(RuntimeError):
    """Invalid trusted binding or observation; no receipt has been issued."""


@dataclass(frozen=True, slots=True)
class LocalParentMonitorBinding:
    """Operator-composed source and registered wire scopes, not an authorization."""
    execution_scope: object
    wire_scope: WireScope

    def __post_init__(self):
        from bt_api_execution.contracts import ExecutionScope

        execution, wire = self.execution_scope, self.wire_scope
        if (type(execution) is not ExecutionScope or type(wire) is not WireScope
                or wire.account_ref != execution.account_ref
                or wire.strategy_ref != execution.strategy_id
                or wire.account_refs or wire.manual_book_ref is not None
                or wire.portfolio_ref is not None or wire.group_id is not None
                or wire.leg_id is not None):
            raise LocalParentMonitorSinkError('INVALID_SCOPE_BINDING')


class LocalParentMonitorServiceSink:
    """Deliver one observation to a borrowed, already registered Monitor sink.

    ``binding`` must be chosen by the trusted owner for the actual local reader;
    in particular the execution domain cannot be inferred from provider names.
    The caller must give this same source scope to LocalParentMonitorRelay and
    run both objects on the sink runtime's owner thread.
    """
    def __init__(self, *, sink, binding, reader):
        if (type(sink) is not ServiceMonitorOutboxSink
                or type(binding) is not LocalParentMonitorBinding
                or not _reader_is_supported(reader)
                or reader._scope != binding.execution_scope
                or sink.outbox_scope != LOCAL_PARENT_OUTBOX_SCOPE):
            raise LocalParentMonitorSinkError('INVALID_CONFIGURATION')
        self._sink = sink
        self._binding = binding
        self._reader = reader
        self._consumer_id = sink.consumer_id
        self._check_binding()

    @property
    def consumer_id(self):
        return self._consumer_id

    def _check_binding(self):
        try:
            self._sink._validate_current_binding()
            _, registered, _ = _current_binding(self._sink._runtime, self._sink._scope_ref)
            if (self._reader._scope != self._binding.execution_scope
                    or registered.scope != self._binding.wire_scope
                    or self._sink.consumer_id != self._consumer_id
                    or self._sink.outbox_scope != LOCAL_PARENT_OUTBOX_SCOPE):
                raise ValueError
        except Exception:
            raise LocalParentMonitorSinkError('INGRESS_BINDING_UNAVAILABLE') from None

    def __call__(self, event):
        if (type(event) is not LocalParentMonitorEvent
                or type(event.event_id) is not str or _EVENT_ID.fullmatch(event.event_id) is None
                or type(event.fingerprint) is not str or _FINGERPRINT.fullmatch(event.fingerprint) is None
                or type(event.sequence) is not int or not 1 <= event.sequence < (1 << 63)
                or type(event.occurred_at_ns) is not int or not 0 <= event.occurred_at_ns <= 253402300799000000000
                or event.event_type != 'LOCAL_RECEIVED' or event.coverage != 'FROM_ACTIVATION'):
            raise LocalParentMonitorSinkError('INVALID_OBSERVATION')
        from bt_api_monitor.durable import OutboxEvent, SequencedOutboxEvent

        self._check_binding()
        item = SequencedOutboxEvent(event.sequence, OutboxEvent(
            event_id=event.event_id, scope=LOCAL_PARENT_OUTBOX_SCOPE,
            event_type=LOCAL_PARENT_EVENT_TYPE,
            data={'schema': 'sdk.local_parent_monitor.v1', 'observation': event.event_type,
                  'coverage': event.coverage, 'sequence': event.sequence,
                  'fingerprint': event.fingerprint},
            occurred_at=event.occurred_at_ns / 1_000_000_000,
        ))
        # This sink returns only after checking the correlated durable ingress
        # receipt. Unknown/invalid responses propagate and cannot yield an ACK.
        if self._sink(item) is not None:
            raise LocalParentMonitorSinkError('INVALID_SINK_RESULT')
        self._check_binding()
        return LocalParentMonitorAck(self._consumer_id, event.event_id, event.fingerprint)


def create_local_parent_service_relay(*, sink, binding, reader, checkpoint_path):
    """Bind the same trusted reader and scope to both components in this thread."""
    adapter = LocalParentMonitorServiceSink(sink=sink, binding=binding, reader=reader)
    return LocalParentMonitorRelay(reader=reader, scope=binding.execution_scope,
                                   sink=adapter, checkpoint_path=checkpoint_path)


__all__ = ['LOCAL_PARENT_EVENT_TYPE', 'LOCAL_PARENT_OUTBOX_SCOPE',
           'LocalParentMonitorBinding', 'LocalParentMonitorServiceSink',
           'LocalParentMonitorSinkError', 'create_local_parent_service_relay']
