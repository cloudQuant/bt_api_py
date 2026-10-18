"""Short-lived evidence that one managed CTP OPEN order is live and unfilled.

This module composes the existing callback reconciliation and exact QueryOrder
evaluator.  It does not write Risk proofs, infer a fill, or authorize a
dispatch.  A returned value is accepted only when the issuing reader can
read back the same callback source and Store projection on the same owner
thread before the bounded expiry.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any, ClassVar

from bt_api_execution import (
    ContractValidationError,
    CtpCallbackSessionBindingV1,
    CtpDispatchCommand,
    CtpOrderIdentityReservation,
    CtpOrderTargetProjectionHandle,
    CtpOrderTargetQueryProvenance,
    CtpOwnerCallbackObservationNotReadyV1,
    CtpOwnerCallbackObservationSnapshotV1,
    CtpVerifiedOrderTargetProjection,
    ExecutionScope,
    ExecutionState,
    OrderIntent,
    PositionEffect,
    ProviderObservation,
    payload_sha256,
)
from bt_api_execution.ctp_single_worker_candidate import CtpManagedDispatchBinding
from bt_api_py.ctp_execution_owner import CtpExecutionOwner
from bt_api_py.ctp_order_target_query import CtpOrderTargetQueryEvaluator
from bt_api_py.ctp_service_execution import (
    decode_ctp_provider_order_id,
    encode_ctp_provider_order_id,
)
from bt_api_execution.ctp_single_worker_candidate import stable_managed_command_id


_MAX_EVIDENCE_TTL_NS = 5_000_000_000


def _reject(message: str) -> None:
    raise ContractValidationError(message)


def _reservation_payload(value: CtpOrderIdentityReservation) -> dict[str, Any]:
    return {
        "account_key": value.account_key,
        "trading_day": value.trading_day,
        "scope_key": value.scope_key,
        "managed_intent_id": value.managed_intent_id,
        "runtime_order_id": value.runtime_order_id,
        "order_ref": value.order_ref,
        "created_at_ns": value.created_at_ns,
    }


def _session_payload(value: CtpCallbackSessionBindingV1) -> dict[str, Any]:
    return {field.name: getattr(value, field.name) for field in fields(value)}


def _snapshot_payload(value: CtpOwnerCallbackObservationSnapshotV1) -> dict[str, Any]:
    payload = {field.name: getattr(value, field.name) for field in fields(value)}
    payload["trade_notional"] = format(value.trade_notional, "f")
    return payload


def _provider_observation_payload(value: ProviderObservation) -> dict[str, Any]:
    return {
        "intent_id": value.intent_id,
        "state": value.state.value,
        "provider_order_id": value.provider_order_id,
        "filled_quantity": format(value.filled_quantity, "f"),
        "average_price": None
        if value.average_price is None
        else format(value.average_price, "f"),
        "reason_code": value.reason_code,
        "cumulative_commission": None
        if value.cumulative_commission is None
        else format(value.cumulative_commission, "f"),
        "cumulative_commission_currency": value.cumulative_commission_currency,
    }


def _projection_payload(value: CtpVerifiedOrderTargetProjection) -> dict[str, Any]:
    return value.to_payload()


def _evidence_payload_without_digest(
    *,
    scope: ExecutionScope,
    managed_intent_id: str,
    intent_fingerprint: str,
    instrument_id: str,
    quantity: int,
    reservation: CtpOrderIdentityReservation,
    submit_command_id: str,
    dispatch_binding_sha256: str,
    local_queue_receipt_id: str,
    native_request_id: int,
    session_binding: CtpCallbackSessionBindingV1,
    callback_snapshot: CtpOwnerCallbackObservationSnapshotV1,
    provider_observation: ProviderObservation,
    projection_handle: CtpOrderTargetProjectionHandle,
    verified_at_ns: int,
    expires_at_ns: int,
) -> dict[str, Any]:
    projection = projection_handle.projection
    return {
        "scope": scope.to_payload(),
        "intent": {
            "intent_id": managed_intent_id,
            "fingerprint": intent_fingerprint,
            "instrument_id": instrument_id,
            "position_effect": PositionEffect.OPEN.value,
            "position_side": "NET",
            "position_bucket": "ALL",
            "reduce_only": False,
            "quantity": quantity,
        },
        "reservation": _reservation_payload(reservation),
        "submit": {
            "command_id": submit_command_id,
            "status": "COMPLETED",
            "local_dispatch_outcome": "QUEUED",
            "local_queue_receipt_id": local_queue_receipt_id,
            "native_request_id": native_request_id,
            "dispatch_binding_sha256": dispatch_binding_sha256,
            "session_binding_sha256": session_binding.session_binding_sha256,
        },
        "session_binding": _session_payload(session_binding),
        "callback_snapshot": _snapshot_payload(callback_snapshot),
        "provider_observation": _provider_observation_payload(provider_observation),
        "query_projection": {
            "projection_id": projection_handle.projection_id,
            "projection_sha256": projection_handle.projection_sha256,
            "projection": _projection_payload(projection),
        },
        "verified_at_ns": verified_at_ns,
        "expires_at_ns": expires_at_ns,
    }


@dataclass(frozen=True, slots=True)
class CtpZeroFillTrackedOrderEvidenceV1:
    """Immutable source projection for a callback-ACKed, zero-fill OPEN order.

    The hash is an audit digest, not an authorization token.  Consumers must
    call the issuing reader's ``readback`` with this exact object.  The typed
    callback snapshot and Store-issued query projection are retained verbatim;
    submit and QueryOrder RequestIDs remain separate fields in those sources.
    """

    scope: ExecutionScope
    managed_intent_id: str
    intent_fingerprint: str
    instrument_id: str
    quantity: int
    reservation: CtpOrderIdentityReservation
    submit_command_id: str
    dispatch_binding_sha256: str
    local_queue_receipt_id: str
    native_request_id: int
    session_binding: CtpCallbackSessionBindingV1
    callback_snapshot: CtpOwnerCallbackObservationSnapshotV1
    provider_observation: ProviderObservation
    projection_handle: CtpOrderTargetProjectionHandle
    verified_at_ns: int
    expires_at_ns: int
    evidence_sha256: str

    schema: ClassVar[str] = "ctp_zero_fill_tracked_order_evidence.v1"

    @property
    def projection(self) -> CtpVerifiedOrderTargetProjection:
        return self.projection_handle.projection

    @property
    def provider_order_id(self) -> str:
        value = self.provider_observation.provider_order_id
        if value is None:
            raise ContractValidationError("zero-fill evidence lacks provider order identity")
        return value

    def _payload_without_digest(self) -> dict[str, Any]:
        return _evidence_payload_without_digest(
            scope=self.scope,
            managed_intent_id=self.managed_intent_id,
            intent_fingerprint=self.intent_fingerprint,
            instrument_id=self.instrument_id,
            quantity=self.quantity,
            reservation=self.reservation,
            submit_command_id=self.submit_command_id,
            dispatch_binding_sha256=self.dispatch_binding_sha256,
            local_queue_receipt_id=self.local_queue_receipt_id,
            native_request_id=self.native_request_id,
            session_binding=self.session_binding,
            callback_snapshot=self.callback_snapshot,
            provider_observation=self.provider_observation,
            projection_handle=self.projection_handle,
            verified_at_ns=self.verified_at_ns,
            expires_at_ns=self.expires_at_ns,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            **self._payload_without_digest(),
            "evidence_sha256": self.evidence_sha256,
        }

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not ExecutionScope
            or type(self.reservation) is not CtpOrderIdentityReservation
            or type(self.session_binding) is not CtpCallbackSessionBindingV1
            or type(self.callback_snapshot) is not CtpOwnerCallbackObservationSnapshotV1
            or type(self.provider_observation) is not ProviderObservation
            or type(self.projection_handle) is not CtpOrderTargetProjectionHandle
            or type(self.projection_handle.projection) is not CtpVerifiedOrderTargetProjection
        ):
            raise ContractValidationError("typed CTP zero-fill evidence sources are required")
        projection = self.projection
        provenance = projection.query_provenance
        snapshot = self.callback_snapshot
        observation = self.provider_observation
        if (
            self.scope.provider != "ctp"
            or self.scope.environment != "simulation"
            or self.scope.trading_day is None
            or type(self.managed_intent_id) is not str
            or not self.managed_intent_id
            or type(self.intent_fingerprint) is not str
            or len(self.intent_fingerprint) != 64
            or type(self.instrument_id) is not str
            or not self.instrument_id
            or type(self.quantity) is not int
            or self.quantity <= 0
            or type(self.submit_command_id) is not str
            or not self.submit_command_id
            or type(self.dispatch_binding_sha256) is not str
            or len(self.dispatch_binding_sha256) != 64
            or type(self.local_queue_receipt_id) is not str
            or not self.local_queue_receipt_id
            or type(self.native_request_id) is not int
            or self.native_request_id <= 0
            or type(self.verified_at_ns) is not int
            or type(self.expires_at_ns) is not int
            or not self.verified_at_ns < self.expires_at_ns
            or self.expires_at_ns - self.verified_at_ns > _MAX_EVIDENCE_TTL_NS
            or type(self.evidence_sha256) is not str
            or len(self.evidence_sha256) != 64
        ):
            raise ContractValidationError("invalid CTP zero-fill evidence identity or lifetime")

        scope, reservation, binding = self.scope, self.reservation, self.session_binding
        if (
            reservation.account_key != scope.account_key
            or reservation.scope_key != scope.key
            or reservation.trading_day != scope.trading_day
            or reservation.managed_intent_id != self.managed_intent_id
            or binding.account_key != scope.account_key
            or binding.scope_key != scope.key
            or binding.trading_day != scope.trading_day
            or binding.session_generation_id != snapshot.session_generation_id
            or binding.owner_intent_id != snapshot.owner_intent_id
            or snapshot.account_key != scope.account_key
            or snapshot.scope_key != scope.key
            or snapshot.trading_day != scope.trading_day
            or snapshot.command_id != self.submit_command_id
            or snapshot.owner_intent_id != binding.owner_intent_id
            or snapshot.session_generation_id != binding.session_generation_id
            or snapshot.order_ref != reservation.order_ref
            or snapshot.command_status != "COMPLETED"
            or snapshot.local_dispatch_outcome != "QUEUED"
            or snapshot.provider_state != "ACKNOWLEDGED"
            or snapshot.volume_traded != 0
            or snapshot.filled_quantity != 0
            or snapshot.trade_fact_count != 0
            or snapshot.trade_notional != Decimal(0)
            or snapshot.request_id != self.native_request_id
            or snapshot.front_id != binding.dispatch_front_id
            or snapshot.session_id != binding.dispatch_session_id
            or snapshot.cancel_command_id is not None
            or snapshot.cancel_action_state is not None
            or snapshot.cumulative_commission is not None
            or snapshot.commission_quality != "INCOMPLETE"
            or observation.intent_id != self.managed_intent_id
            or observation.state is not ExecutionState.ACKED
            or observation.filled_quantity != Decimal(0)
            or observation.average_price is not None
            or observation.reason_code is not None
            or observation.cumulative_commission is not None
            or observation.cumulative_commission_currency is not None
            or observation.provider_order_id
            != encode_ctp_provider_order_id(snapshot.exchange_id, snapshot.order_sys_id)
            or decode_ctp_provider_order_id(observation.provider_order_id)
            != (snapshot.exchange_id, snapshot.order_sys_id)
        ):
            raise ContractValidationError("CTP callback and zero-fill observation bindings differ")

        if (
            projection.account_key != scope.account_key
            or projection.scope_key != scope.key
            or projection.trading_day != scope.trading_day
            or projection.managed_intent_id != self.managed_intent_id
            or projection.runtime_order_id != reservation.runtime_order_id
            or projection.order_ref != reservation.order_ref
            or projection.instrument_id != self.instrument_id
            or projection.exchange_id != snapshot.exchange_id
            or projection.order_sys_id != snapshot.order_sys_id
            or projection.session_generation_id != binding.session_generation_id
            or projection.connection_generation != binding.connection_generation
            or projection.query_front_id != binding.dispatch_front_id
            or projection.query_session_id != binding.dispatch_session_id
            or projection.front_id != snapshot.front_id
            or projection.session_id != snapshot.session_id
            or projection.provider_state != "OPEN"
            or projection.quantity != self.quantity
            or projection.traded_quantity != 0
            or projection.remaining_quantity != self.quantity
            or projection.query_record_count != 1
            or projection.query_match_count != 1
            or projection.query_complete is not True
            or projection.query_terminal is not True
            or projection.query_timed_out is not False
            or projection.query_error_id not in (None, 0)
            or projection.late_callback_count != 0
            or type(provenance) is not CtpOrderTargetQueryProvenance
            or provenance.owner_intent_id != binding.owner_intent_id
            or provenance.account_key != scope.account_key
            or provenance.scope_key != scope.key
            or provenance.trading_day != scope.trading_day
            or provenance.managed_intent_id != self.managed_intent_id
            or provenance.runtime_order_id != reservation.runtime_order_id
            or provenance.order_ref != reservation.order_ref
            or provenance.writer_owner_id == ""
            or provenance.session_binding_sha256 != binding.session_binding_sha256
            or provenance.session_generation_id != binding.session_generation_id
            or provenance.connection_generation != binding.connection_generation
            or provenance.dispatch_front_id != binding.dispatch_front_id
            or provenance.dispatch_session_id != binding.dispatch_session_id
            or provenance.applied_source_sequence != provenance.end_source_sequence
            or provenance.start_source_sequence < snapshot.order_source_sequence
            or self.expires_at_ns > projection.expires_at_ns
            or self.projection_handle.projection_sha256
            != payload_sha256(projection.to_payload())
        ):
            raise ContractValidationError("CTP OPEN query projection differs from callback source")

        expected_digest = payload_sha256(
            {"schema": self.schema, **self._payload_without_digest()}
        )
        if expected_digest != self.evidence_sha256:
            raise ContractValidationError("CTP zero-fill evidence digest differs")


@dataclass(frozen=True, slots=True)
class _EvidenceAttempt:
    intent: OrderIntent
    reservation: CtpOrderIdentityReservation
    command: CtpDispatchCommand
    dispatch_binding: CtpManagedDispatchBinding
    session_binding: CtpCallbackSessionBindingV1
    callback_snapshot: CtpOwnerCallbackObservationSnapshotV1
    provider_observation: ProviderObservation
    projection_handle: CtpOrderTargetProjectionHandle
    evidence: CtpZeroFillTrackedOrderEvidenceV1


class CtpZeroFillTrackedOrderReader:
    """Issue and revalidate one callback-ACKed, zero-fill OPEN projection.

    The reader is confined to the exact active ``CtpExecutionOwner`` thread.
    It uses the existing target-query evaluator for the one native QueryOrder
    operation; no Risk receipt, release, or dispatch authority is created.
    """

    def __init__(self, owner: CtpExecutionOwner) -> None:
        if type(owner) is not CtpExecutionOwner:
            _reject("exact borrowed CTP execution owner is required")
        owner._assert_worker_thread()
        binding = owner._assert_active_owner()
        self._owner = owner
        self._store = owner._store
        self._scope = owner._scope
        self._writer_lease = owner._writer_lease
        self._owner_handle = owner._owner_handle
        self._thread = threading.current_thread()
        self._initial_binding = binding
        self._query = CtpOrderTargetQueryEvaluator(owner)
        self._busy = False
        self._issued: CtpZeroFillTrackedOrderEvidenceV1 | None = None
        self._attempt: _EvidenceAttempt | None = None

    def _assert_thread_and_owner(self) -> CtpCallbackSessionBindingV1:
        if threading.current_thread() is not self._thread:
            _reject("CTP zero-fill reader is confined to its owner thread")
        self._owner._assert_worker_thread()
        binding = self._owner._assert_active_owner()
        if binding != self._initial_binding:
            _reject("borrowed CTP session binding changed")
        return binding

    def _read_target(
        self, managed_intent_id: str, binding: CtpCallbackSessionBindingV1
    ) -> tuple[OrderIntent, CtpOrderIdentityReservation, CtpDispatchCommand, CtpManagedDispatchBinding]:
        if type(managed_intent_id) is not str or not managed_intent_id:
            _reject("managed CTP intent ID is required")
        intent = self._store.get_intent(managed_intent_id, scope=self._scope)
        reservation = self._store.read_ctp_order_identity(self._scope, managed_intent_id)
        if type(intent) is not OrderIntent or type(reservation) is not CtpOrderIdentityReservation:
            _reject("durable CTP intent and identity reservation are required")
        if (
            intent.scope != self._scope
            or intent.position_effect is not PositionEffect.OPEN
            or intent.reduce_only is not False
            or intent.position_side != "NET"
            or intent.position_bucket != "ALL"
            or reservation.account_key != self._scope.account_key
            or reservation.scope_key != self._scope.key
            or reservation.trading_day != self._scope.trading_day
            or reservation.managed_intent_id != intent.intent_id
            or binding.account_key != self._scope.account_key
            or binding.scope_key != self._scope.key
            or binding.trading_day != self._scope.trading_day
        ):
            _reject("CTP OPEN intent, reservation, and active session differ")
        command_id = stable_managed_command_id(
            "submit", managed_intent_id, reservation.runtime_order_id
        )
        command = self._store.read_ctp_dispatch_command(self._scope, command_id)
        if type(command) is not CtpDispatchCommand:
            _reject("durable CTP SUBMIT command is required")
        dispatch_binding = CtpManagedDispatchBinding.from_command(command, reservation)
        if (
            command.command_id != command_id
            or command.operation != "SUBMIT"
            or command.status != "COMPLETED"
            or command.account_key != self._scope.account_key
            or command.scope_key != self._scope.key
            or command.trading_day != self._scope.trading_day
            or command.reservation_managed_intent_id != intent.intent_id
            or command.order_ref != reservation.order_ref
            or command.session_binding_sha256 != binding.session_binding_sha256
            or command.local_queue_receipt_queued is not True
            or dispatch_binding.operation != "submit"
            or dispatch_binding.managed_intent_id != intent.intent_id
            or dispatch_binding.runtime_order_id != reservation.runtime_order_id
            or dispatch_binding.order_ref != reservation.order_ref
            or dispatch_binding.account_key != self._scope.account_key
            or dispatch_binding.scope_key != self._scope.key
            or dispatch_binding.trading_day != self._scope.trading_day
            or dispatch_binding.session_binding_sha256 != binding.session_binding_sha256
            or dispatch_binding.session_generation_id != binding.session_generation_id
            or dispatch_binding.dispatch_front_id != binding.dispatch_front_id
            or dispatch_binding.dispatch_session_id != binding.dispatch_session_id
            or dispatch_binding.local_queue_receipt_queued is not True
        ):
            _reject("CTP SUBMIT command is not an exact completed queued source")
        return intent, reservation, command, dispatch_binding

    def _read_source(
        self,
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        command: CtpDispatchCommand,
        dispatch_binding: CtpManagedDispatchBinding,
        binding: CtpCallbackSessionBindingV1,
    ) -> tuple[
        CtpOwnerCallbackObservationSnapshotV1 | CtpOwnerCallbackObservationNotReadyV1,
        ProviderObservation | CtpOwnerCallbackObservationNotReadyV1,
    ]:
        snapshot = self._store.read_ctp_owner_callback_observation_snapshot(
            self._scope, command.command_id
        )
        if type(snapshot) is CtpOwnerCallbackObservationNotReadyV1:
            return snapshot, snapshot
        if type(snapshot) is not CtpOwnerCallbackObservationSnapshotV1:
            _reject("exact typed CTP callback snapshot is required")
        self._validate_snapshot(snapshot, intent, reservation, command, binding)
        observation = self._owner.read_provider_observation(intent, dispatch_binding)
        if type(observation) is CtpOwnerCallbackObservationNotReadyV1:
            return snapshot, observation
        self._validate_observation(observation, snapshot, intent)
        return snapshot, observation

    def _validate_snapshot(
        self,
        snapshot: CtpOwnerCallbackObservationSnapshotV1,
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        command: CtpDispatchCommand,
        binding: CtpCallbackSessionBindingV1,
    ) -> None:
        if (
            snapshot.account_key != self._scope.account_key
            or snapshot.scope_key != self._scope.key
            or snapshot.trading_day != self._scope.trading_day
            or snapshot.command_id != command.command_id
            or snapshot.owner_intent_id != binding.owner_intent_id
            or snapshot.session_generation_id != binding.session_generation_id
            or snapshot.order_ref != reservation.order_ref
            or snapshot.command_status != "COMPLETED"
            or snapshot.local_dispatch_outcome != "QUEUED"
            or snapshot.provider_state != "ACKNOWLEDGED"
            or command.correlation_key is None
            or snapshot.request_id != command.correlation_key.native_request_id
            or snapshot.front_id != binding.dispatch_front_id
            or snapshot.session_id != binding.dispatch_session_id
            or snapshot.volume_traded != 0
            or snapshot.filled_quantity != 0
            or snapshot.trade_fact_count != 0
            or snapshot.trade_notional != Decimal(0)
            or snapshot.cancel_command_id is not None
            or snapshot.cancel_action_state is not None
            or snapshot.cumulative_commission is not None
            or snapshot.commission_quality != "INCOMPLETE"
            or intent.intent_id != reservation.managed_intent_id
        ):
            _reject("CTP callback source is not the exact zero-fill OPEN target")

    @staticmethod
    def _validate_observation(
        observation: ProviderObservation,
        snapshot: CtpOwnerCallbackObservationSnapshotV1,
        intent: OrderIntent,
    ) -> None:
        expected_provider_id = encode_ctp_provider_order_id(
            snapshot.exchange_id, snapshot.order_sys_id
        )
        if (
            type(observation) is not ProviderObservation
            or observation.intent_id != intent.intent_id
            or observation.state is not ExecutionState.ACKED
            or observation.provider_order_id != expected_provider_id
            or decode_ctp_provider_order_id(observation.provider_order_id)
            != (snapshot.exchange_id, snapshot.order_sys_id)
            or observation.filled_quantity != Decimal(0)
            or observation.average_price is not None
            or observation.reason_code is not None
            or observation.cumulative_commission is not None
            or observation.cumulative_commission_currency is not None
        ):
            _reject("CTP provider observation differs from exact zero-fill ACK")

    def _validate_projection(
        self,
        handle: CtpOrderTargetProjectionHandle,
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        snapshot: CtpOwnerCallbackObservationSnapshotV1,
        binding: CtpCallbackSessionBindingV1,
    ) -> CtpVerifiedOrderTargetProjection:
        if (
            type(handle) is not CtpOrderTargetProjectionHandle
            or type(handle.projection) is not CtpVerifiedOrderTargetProjection
        ):
            _reject("Store-issued CTP order target projection handle is required")
        projection = handle.projection
        provenance = projection.query_provenance
        if (
            projection.account_key != self._scope.account_key
            or projection.scope_key != self._scope.key
            or projection.trading_day != self._scope.trading_day
            or projection.managed_intent_id != intent.intent_id
            or projection.runtime_order_id != reservation.runtime_order_id
            or projection.order_ref != reservation.order_ref
            or projection.instrument_id != intent.instrument
            or projection.exchange_id != snapshot.exchange_id
            or projection.order_sys_id != snapshot.order_sys_id
            or projection.session_generation_id != binding.session_generation_id
            or projection.connection_generation != binding.connection_generation
            or projection.query_front_id != binding.dispatch_front_id
            or projection.query_session_id != binding.dispatch_session_id
            or projection.front_id != snapshot.front_id
            or projection.session_id != snapshot.session_id
            or projection.provider_state != "OPEN"
            or projection.quantity != int(intent.quantity)
            or projection.traded_quantity != 0
            or projection.remaining_quantity != int(intent.quantity)
            or projection.query_record_count != 1
            or projection.query_match_count != 1
            or projection.query_complete is not True
            or projection.query_terminal is not True
            or projection.query_timed_out is not False
            or projection.query_error_id not in (None, 0)
            or projection.late_callback_count != 0
            or type(provenance) is not CtpOrderTargetQueryProvenance
            or provenance.owner_intent_id != binding.owner_intent_id
            or provenance.account_key != self._scope.account_key
            or provenance.scope_key != self._scope.key
            or provenance.trading_day != self._scope.trading_day
            or provenance.managed_intent_id != intent.intent_id
            or provenance.runtime_order_id != reservation.runtime_order_id
            or provenance.order_ref != reservation.order_ref
            or provenance.writer_owner_id != self._writer_lease.owner_id
            or provenance.writer_fencing_token != self._writer_lease.fencing_token
            or provenance.session_binding_sha256 != binding.session_binding_sha256
            or provenance.session_generation_id != binding.session_generation_id
            or provenance.connection_generation != binding.connection_generation
            or provenance.dispatch_front_id != binding.dispatch_front_id
            or provenance.dispatch_session_id != binding.dispatch_session_id
            or provenance.applied_source_sequence != provenance.end_source_sequence
            or provenance.start_source_sequence < snapshot.order_source_sequence
            or handle.projection_sha256 != payload_sha256(projection.to_payload())
        ):
            _reject("CTP OPEN query projection differs from the exact callback source")
        return projection

    def issue(
        self, managed_intent_id: str, *, timeout: float = 5.0
    ) -> CtpZeroFillTrackedOrderEvidenceV1 | CtpOwnerCallbackObservationNotReadyV1:
        """Query and issue evidence for one exact durable managed intent."""

        if threading.current_thread() is not self._thread:
            _reject("CTP zero-fill reader is confined to its owner thread")
        if self._busy or self._issued is not None:
            _reject("CTP zero-fill reader is busy or already issued evidence")
        self._busy = True
        try:
            binding = self._assert_thread_and_owner()
            intent, reservation, command, dispatch_binding = self._read_target(
                managed_intent_id, binding
            )
            snapshot, observation = self._read_source(
                intent, reservation, command, dispatch_binding, binding
            )
            if type(snapshot) is CtpOwnerCallbackObservationNotReadyV1:
                return snapshot
            if type(observation) is CtpOwnerCallbackObservationNotReadyV1:
                return observation

            handle = self._query.query_and_issue(
                intent.intent_id,
                instrument_id=intent.instrument,
                exchange_id=snapshot.exchange_id,
                order_sys_id=snapshot.order_sys_id,
                timeout=timeout,
            )
            projection = self._validate_projection(
                handle, intent, reservation, snapshot, binding
            )

            current_binding = self._assert_thread_and_owner()
            current_target = self._read_target(intent.intent_id, current_binding)
            if (
                current_binding != binding
                or current_target[0] != intent
                or current_target[1] != reservation
                or current_target[2] != command
                or current_target[3] != dispatch_binding
            ):
                _reject("CTP managed OPEN target changed during QueryOrder")
            current_snapshot, current_observation = self._read_source(
                intent, reservation, command, dispatch_binding, current_binding
            )
            if (
                type(current_snapshot) is not CtpOwnerCallbackObservationSnapshotV1
                or current_snapshot != snapshot
                or type(current_observation) is not ProviderObservation
                or current_observation != observation
            ):
                _reject("CTP callback source changed during QueryOrder")
            if self._store.read_ctp_order_target_projection(self._scope, handle) != projection:
                _reject("CTP query projection failed same-Store readback")

            verified_at_ns = time.monotonic_ns()
            expires_at_ns = min(
                verified_at_ns + _MAX_EVIDENCE_TTL_NS,
                projection.expires_at_ns,
            )
            base = {
                "scope": self._scope,
                "managed_intent_id": intent.intent_id,
                "intent_fingerprint": intent.fingerprint,
                "instrument_id": intent.instrument,
                "quantity": int(intent.quantity),
                "reservation": reservation,
                "submit_command_id": command.command_id,
                "dispatch_binding_sha256": payload_sha256(dispatch_binding.to_payload()),
                "local_queue_receipt_id": dispatch_binding.local_queue_receipt_id,
                "native_request_id": dispatch_binding.native_request_id,
                "session_binding": binding,
                "callback_snapshot": snapshot,
                "provider_observation": observation,
                "projection_handle": handle,
                "verified_at_ns": verified_at_ns,
                "expires_at_ns": expires_at_ns,
            }
            evidence_digest = payload_sha256(
                {
                    "schema": CtpZeroFillTrackedOrderEvidenceV1.schema,
                    **_evidence_payload_without_digest(**base),
                }
            )
            evidence = CtpZeroFillTrackedOrderEvidenceV1(
                **base, evidence_sha256=evidence_digest
            )
            self._issued = evidence
            self._attempt = _EvidenceAttempt(
                intent=intent,
                reservation=reservation,
                command=command,
                dispatch_binding=dispatch_binding,
                session_binding=binding,
                callback_snapshot=snapshot,
                provider_observation=observation,
                projection_handle=handle,
                evidence=evidence,
            )
            return evidence
        finally:
            self._busy = False

    def readback(
        self, evidence: CtpZeroFillTrackedOrderEvidenceV1
    ) -> CtpZeroFillTrackedOrderEvidenceV1:
        """Revalidate the exact issued evidence against current Store sources."""

        attempt = self._attempt
        if (
            threading.current_thread() is not self._thread
            or self._busy
            or type(evidence) is not CtpZeroFillTrackedOrderEvidenceV1
            or self._issued is not evidence
            or attempt is None
            or attempt.evidence is not evidence
            or time.monotonic_ns() >= evidence.expires_at_ns
        ):
            _reject("exact unexpired CTP zero-fill evidence is required")
        self._busy = True
        try:
            evidence.__post_init__()
            binding = self._assert_thread_and_owner()
            if binding != attempt.session_binding:
                _reject("CTP callback session changed after evidence issuance")
            target = self._read_target(attempt.intent.intent_id, binding)
            if (
                target[0] != attempt.intent
                or target[1] != attempt.reservation
                or target[2] != attempt.command
                or target[3] != attempt.dispatch_binding
                or payload_sha256(target[3].to_payload())
                != evidence.dispatch_binding_sha256
            ):
                _reject("CTP command or reservation changed after evidence issuance")
            snapshot, observation = self._read_source(
                attempt.intent,
                attempt.reservation,
                attempt.command,
                attempt.dispatch_binding,
                binding,
            )
            if (
                type(snapshot) is not CtpOwnerCallbackObservationSnapshotV1
                or snapshot != attempt.callback_snapshot
                or type(observation) is not ProviderObservation
                or observation != attempt.provider_observation
            ):
                _reject("CTP callback source changed after evidence issuance")
            projection = self._store.read_ctp_order_target_projection(
                self._scope, attempt.projection_handle
            )
            if (
                projection != evidence.projection
                or self._validate_projection(
                    attempt.projection_handle,
                    attempt.intent,
                    attempt.reservation,
                    attempt.callback_snapshot,
                    binding,
                )
                != projection
            ):
                _reject("CTP Store query projection changed after evidence issuance")
            if time.monotonic_ns() >= evidence.expires_at_ns:
                _reject("CTP zero-fill evidence expired during readback")
            return evidence
        finally:
            self._busy = False


__all__ = [
    "CtpZeroFillTrackedOrderEvidenceV1",
    "CtpZeroFillTrackedOrderReader",
]
