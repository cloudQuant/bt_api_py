"""Short-lived evidence that one managed CTP OPEN order is live and unfilled.

This module composes the existing callback reconciliation and exact QueryOrder
evaluator.  It does not write Risk proofs, infer a fill, or authorize a
dispatch.  A returned value is accepted only when the issuing reader can
read back the same callback source and Store projection on the same owner
thread before the bounded expiry.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal
from math import isfinite
from typing import Any, ClassVar

from bt_api_ctp.containers.ctp.ctp_native_query_certificate import (
    CtpNativeQueryCertificateError,
    _query_records_digest,
    _query_result_shape,
    _record_value,
    _same_scope,
    _scope_for_client,
    _source_for_result,
)
from bt_api_ctp.ctp.callback_ingress import SAFE_TRADE_FIELD_NAMES
from bt_api_ctp.query import QueryResult
from bt_api_execution import (
    ContractValidationError,
    CtpCallbackSessionBindingV1,
    CtpDispatchCommand,
    CtpOrderIdentityReservation,
    CtpOrderTargetProjectionHandle,
    CtpOrderTargetQueryFence,
    CtpOrderTargetQueryProvenance,
    CtpOrderTargetQueryWindow,
    CtpOwnerCallbackObservationNotReadyV1,
    CtpOwnerCallbackObservationSnapshotV1,
    CtpVerifiedOrderTargetProjection,
    ExecutionScope,
    ExecutionState,
    OrderIntent,
    PositionEffect,
    ProviderObservation,
    canonical_json,
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
_MAX_QUERY_TRADE_RECORDS = 128
_MAX_ISSUE_TIMEOUT_SECONDS = 20.0
_QUERY_TRADE_REQUIRED_FIELDS = (
    "BrokerID",
    "InvestorID",
    "UserID",
    "InstrumentID",
    "ExchangeID",
    "OrderRef",
    "OrderSysID",
    "TradeID",
    "TradingDay",
    "TradeDate",
    "TradeTime",
    "Direction",
    "OffsetFlag",
    "HedgeFlag",
    "Price",
    "Volume",
)


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


@dataclass(frozen=True, slots=True)
class _CtpTradeQueryWindowEvidence:
    """Same-client QueryResult bound to its applied Store callback window."""

    query_scope: Any
    fence: CtpOrderTargetQueryFence
    window: CtpOrderTargetQueryWindow
    result: QueryResult[Any]
    source: Any
    records_sha256: str
    filters_sha256: str
    target_trade_count: int
    intent_instrument_id: str
    target_order_sys_id: str

    def __post_init__(self) -> None:
        source = self.source
        result = self.result
        expected_filters = tuple(getattr(source, "request_intent_filters", ()))
        if (
            type(self.fence) is not CtpOrderTargetQueryFence
            or type(self.window) is not CtpOrderTargetQueryWindow
            or type(result) is not QueryResult
            or source is not result.query_source
            or self.window.fence_id != self.fence.fence_id
            or self.window.owner_handle is not self.fence.owner_handle
            or self.window.query_request_id != result.request_id
            or result.request_type != "trades"
            or result.complete is not True
            or result.is_last_seen is not True
            or result.timed_out is not False
            or result.unsupported is not False
            or result.error_code not in (None, 0)
            or result.submit_code != 0
            or result.error_message != ""
            or result.late_callback_count != 0
            or type(result.records) is not tuple
            or len(result.records) > _MAX_QUERY_TRADE_RECORDS
            or expected_filters != tuple(getattr(source, "request_filters", ()))
            or len(self.window.events) < 1
            or len(self.window.events) > _MAX_QUERY_TRADE_RECORDS + 1
            or type(self.records_sha256) is not str
            or len(self.records_sha256) != 64
            or type(self.filters_sha256) is not str
            or len(self.filters_sha256) != 64
            or type(self.target_trade_count) is not int
            or self.target_trade_count != 0
            or type(self.intent_instrument_id) is not str
            or not self.intent_instrument_id
            or type(self.target_order_sys_id) is not str
            or not self.target_order_sys_id
        ):
            _reject("CTP trade query and Store window evidence are not bound")
        try:
            actual_records_sha256 = _query_records_digest(result.records)
        except (TypeError, ValueError) as error:
            _reject("CTP trade query rows are no longer canonical", cause=error)
        expected_filters_sha256 = payload_sha256(
            [[name, value] for name, value in expected_filters]
        )
        if (
            actual_records_sha256 != self.records_sha256
            or getattr(source, "records_sha256", None) != self.records_sha256
            or expected_filters_sha256 != self.filters_sha256
        ):
            _reject("CTP trade query rows or filters changed after terminal completion")


def _trade_query_payload(value: _CtpTradeQueryWindowEvidence) -> dict[str, Any]:
    result = value.result
    fence = value.fence
    window = value.window
    source = value.source
    return {
        "schema": "ctp_order_target_trade_query_window.v1",
        "instrument_id": value.intent_instrument_id,
        "account_key": fence.account_key,
        "scope_key": fence.scope_key,
        "trading_day": fence.trading_day,
        "managed_intent_id": fence.managed_intent_id,
        "runtime_order_id": fence.runtime_order_id,
        "order_ref": fence.order_ref,
        "target_order_sys_id": value.target_order_sys_id,
        "owner_intent_id": fence.owner_handle.owner_intent_id,
        "session_binding_sha256": fence.session_binding_sha256,
        "session_generation_id": fence.session_generation_id,
        "connection_generation": fence.connection_generation,
        "query_request_id": result.request_id,
        "record_count": len(result.records),
        "records_sha256": value.records_sha256,
        "filters_sha256": value.filters_sha256,
        "complete": result.complete,
        "is_last_seen": result.is_last_seen,
        "timed_out": result.timed_out,
        "unsupported": result.unsupported,
        "error_code": result.error_code,
        "late_callback_count": result.late_callback_count,
        "store_fence_id": fence.fence_id,
        "store_window_sha256": window.window_sha256,
        "store_start_source_sequence": window.start_source_sequence,
        "store_end_source_sequence": window.end_source_sequence,
        "store_applied_source_sequence": window.applied_source_sequence,
        "store_observed_at_ns": window.observed_at_ns,
        "query_started_monotonic": source.started_monotonic,
        "query_completed_monotonic": source.completed_monotonic,
        "target_order_trade_count": value.target_trade_count,
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
    trade_query: _CtpTradeQueryWindowEvidence,
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
        "query_trade": _trade_query_payload(trade_query),
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
    trade_query: _CtpTradeQueryWindowEvidence
    verified_at_ns: int
    expires_at_ns: int
    evidence_sha256: str

    schema: ClassVar[str] = "ctp_zero_fill_tracked_order_evidence.v2"

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
            trade_query=self.trade_query,
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
            or type(self.trade_query) is not _CtpTradeQueryWindowEvidence
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
        trade_fence = self.trade_query.fence
        trade_window = self.trade_query.window
        trade_result = self.trade_query.result
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

        if (
            trade_fence.owner_handle is not self.trade_query.window.owner_handle
            or trade_fence.owner_handle.owner_intent_id != binding.owner_intent_id
            or trade_fence.account_key != scope.account_key
            or trade_fence.scope_key != scope.key
            or trade_fence.trading_day != scope.trading_day
            or trade_fence.managed_intent_id != self.managed_intent_id
            or trade_fence.runtime_order_id != reservation.runtime_order_id
            or trade_fence.order_ref != reservation.order_ref
            or trade_fence.session_binding_sha256 != binding.session_binding_sha256
            or trade_fence.session_generation_id != binding.session_generation_id
            or trade_fence.connection_generation != binding.connection_generation
            or trade_fence.dispatch_front_id != binding.dispatch_front_id
            or trade_fence.dispatch_session_id != binding.dispatch_session_id
            or trade_fence.source_tags
            != (
                binding.source_instance_id,
                binding.native_client_epoch,
                binding.native_api_source_id,
                binding.native_spi_source_id,
                binding.native_api_generation,
                binding.source_connection_generation,
            )
            or self.trade_query.query_scope.trading_day != binding.trading_day
            or self.trade_query.query_scope.connection_generation
            != binding.connection_generation
            or trade_result.request_type != "trades"
            or trade_result.connection_generation != binding.connection_generation
            or trade_result.complete is not True
            or trade_result.is_last_seen is not True
            or trade_result.timed_out is not False
            or trade_result.unsupported is not False
            or trade_result.error_code not in (None, 0)
            or trade_result.late_callback_count != 0
            or trade_result.submit_code != 0
            or trade_result.error_message != ""
            or trade_result.records.__class__ is not tuple
            or len(trade_result.records) > _MAX_QUERY_TRADE_RECORDS
            or self.trade_query.intent_instrument_id != self.instrument_id
            or self.trade_query.target_order_sys_id != snapshot.order_sys_id
            or self.trade_query.query_scope.trading_day != binding.trading_day
            or self.trade_query.query_scope.connection_generation
            != binding.connection_generation
            or self.trade_query.query_scope.account_fingerprint
            != trade_result.account_fingerprint
            or self.trade_query.source.broker_id
            != self.trade_query.query_scope.broker_id
            or self.trade_query.source.investor_id
            != self.trade_query.query_scope.investor_id
            or trade_window.applied_source_sequence != trade_window.end_source_sequence
            or trade_window.observed_at_ns < trade_fence.created_at_ns
            or trade_window.observed_at_ns >= trade_fence.expires_at_ns
            or self.trade_query.target_trade_count != 0
        ):
            raise ContractValidationError("CTP zero-fill trade query is not a complete scoped window")

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
    trade_query: _CtpTradeQueryWindowEvidence
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

    @staticmethod
    def _trade_row(record: Any, query_scope: Any, intent: OrderIntent, exchange_id: str) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for name in SAFE_TRADE_FIELD_NAMES:
            try:
                present, value = _record_value(record, name)
            except CtpNativeQueryCertificateError as error:
                _reject("CTP QueryTrade row is unreadable", cause=error)
            if not present:
                _reject("CTP QueryTrade row is missing an allowlisted field")
            row[name] = value
        for name in _QUERY_TRADE_REQUIRED_FIELDS:
            value = row[name]
            if name not in {"Price", "Volume"} and (
                type(value) is not str or not value or value != value.strip()
            ):
                _reject("CTP QueryTrade row identity is incomplete")
        if (
            row["BrokerID"] != query_scope.broker_id
            or row["InvestorID"] != query_scope.investor_id
            or row["UserID"] != query_scope.investor_id
            or row["TradingDay"] != query_scope.trading_day
            or row["InstrumentID"] != intent.instrument
            or row["ExchangeID"] != exchange_id
        ):
            _reject("CTP QueryTrade row differs from the exact account/session filters")
        if (
            type(row["TradeID"]) is not str
            or not row["TradeID"]
            or type(row["OrderRef"]) is not str
            or not row["OrderRef"]
            or type(row["OrderSysID"]) is not str
            or not row["OrderSysID"]
            or type(row["Price"]) is not float
            or not isfinite(row["Price"])
            or row["Price"] <= 0
            or type(row["Volume"]) is not int
            or row["Volume"] <= 0
        ):
            _reject("CTP QueryTrade row has invalid identity, price, or volume")
        if any(
            type(row[name]) is not str or not row[name]
            for name in (
                "TradeDate",
                "TradeTime",
                "Direction",
                "OffsetFlag",
                "HedgeFlag",
            )
        ):
            _reject("CTP QueryTrade row is missing native trade fields")
        return row

    @staticmethod
    def _validate_trade_query_window(
        window: CtpOrderTargetQueryWindow,
        fence: CtpOrderTargetQueryFence,
        result: QueryResult[Any],
        rows: tuple[dict[str, Any], ...],
    ) -> None:
        if (
            type(window) is not CtpOrderTargetQueryWindow
            or window.fence_id != fence.fence_id
            or window.owner_handle is not fence.owner_handle
            or window.query_request_id != result.request_id
            or window.start_source_sequence != fence.start_source_sequence
            or window.applied_source_sequence != window.end_source_sequence
            or window.observed_at_ns < fence.created_at_ns
            or window.observed_at_ns >= fence.expires_at_ns
            or not 1 <= len(window.events) <= 256
        ):
            _reject("CTP QueryTrade Store window is outside its exact fence")

        callback_rows: list[dict[str, Any]] = []
        for index, event in enumerate(window.events):
            if (
                event.owner_handle is not fence.owner_handle
                or event.source_sequence != fence.start_source_sequence + index + 1
                or event.callback_name != "OnRspQryTrade"
                or event.callback_class != "AUDIT_QUERY"
                or event.source_phase != "ACTIVE"
                or event.source_tags != fence.source_tags
                or event.connection_generation != fence.connection_generation
            ):
                _reject("CTP QueryTrade callback source differs from its Store fence")
            try:
                payload = json.loads(event.record_payload_json)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                _reject("CTP QueryTrade callback payload is unreadable", cause=error)
            if (
                type(payload) is not dict
                or canonical_json(payload) != event.record_payload_json
                or payload.get("schema") != "ctp_trader_callback_ingress.v2"
                or payload.get("owner_intent_id") != fence.owner_handle.owner_intent_id
                or payload.get("callback_name") != "OnRspQryTrade"
                or payload.get("callback_class") != "AUDIT_QUERY"
                or payload.get("phase") != "ACTIVE"
                or payload.get("sequence") != event.source_sequence
                or payload.get("digest") != event.record_digest_sha256
                or payload.get("capture_complete") is not True
                or payload.get("missing_getters") != []
                or payload.get("connection_generation") != fence.connection_generation
                or payload.get("source_instance_id") != fence.source_tags[0]
                or payload.get("native_client_epoch") != fence.source_tags[1]
                or payload.get("native_api_source_id") != fence.source_tags[2]
                or payload.get("native_spi_source_id") != fence.source_tags[3]
                or payload.get("native_api_generation") != fence.source_tags[4]
                or payload.get("source_connection_generation") != fence.source_tags[5]
            ):
                _reject("CTP QueryTrade callback payload differs from its Store receipt")

            named = payload.get("named_args")
            if (
                type(named) is not list
                or len(named) != 4
                or named[0]
                != {
                    "argument_slot": 0,
                    "name": "pTrade",
                    "present": True,
                    "scalar_captured": False,
                    "value": None,
                }
                or any(type(item) is not dict for item in named[1:])
                or named[1].get("name") != "pRspInfo"
                or named[1].get("argument_slot") != 1
                or named[1].get("present") is not True
                or named[1].get("scalar_captured") is not False
                or named[1].get("value") is not None
                or named[2].get("name") != "nRequestID"
                or named[2].get("argument_slot") != 2
                or named[2].get("present") is not True
                or named[2].get("scalar_captured") is not True
                or type(named[2].get("value")) is not int
                or named[2].get("value") != result.request_id
                or named[3].get("name") != "bIsLast"
                or named[3].get("argument_slot") != 3
                or named[3].get("present") is not True
                or named[3].get("scalar_captured") is not True
                or type(named[3].get("value")) is not bool
                or named[3].get("value") is not (index == len(window.events) - 1)
            ):
                _reject("CTP QueryTrade callback request cursor is invalid")

            flattened = payload.get("flattened_fields")
            if type(flattened) is not list or any(
                type(item) is not dict for item in flattened
            ):
                _reject("CTP QueryTrade callback fields are not captured")
            if any(
                type(item.get("field_name")) is not str
                or type(item.get("argument_slot")) is not int
                for item in flattened
            ):
                _reject("CTP QueryTrade callback field identity is invalid")
            row_fields = {
                item.get("field_name"): item
                for item in flattened
                if item.get("argument_slot") == 0
            }
            response_fields = {
                item.get("field_name"): item
                for item in flattened
                if item.get("argument_slot") == 1
            }
            if set(response_fields) != {"ErrorID"}:
                _reject("CTP QueryTrade response info is incomplete")
            if len(row_fields) != sum(
                item.get("argument_slot") == 0 for item in flattened
            ):
                _reject("CTP QueryTrade callback has duplicate trade fields")
            error_item = response_fields["ErrorID"]
            if (
                error_item.get("present") is not True
                or error_item.get("scalar_captured") is not True
                or type(error_item.get("value")) is not int
                or error_item.get("value") != 0
            ):
                _reject("CTP QueryTrade callback reports a provider error")
            if (
                len(row_fields) != len(SAFE_TRADE_FIELD_NAMES)
                or set(row_fields) != set(SAFE_TRADE_FIELD_NAMES)
            ):
                _reject("CTP QueryTrade callback trade field set is incomplete")
            if all(
                item.get("present") is False
                and item.get("scalar_captured") is False
                and item.get("value") is None
                for item in row_fields.values()
            ):
                if index != len(window.events) - 1 or named[3].get("value") is not True:
                    _reject("CTP QueryTrade empty callback is not its terminal packet")
                continue
            if any(
                item.get("present") is not True
                or item.get("scalar_captured") is not True
                for item in row_fields.values()
            ):
                _reject("CTP QueryTrade callback row is only partially captured")
            callback_row = {
                name: item.get("value") for name, item in row_fields.items()
            }
            callback_rows.append(callback_row)

        if len(callback_rows) != len(rows):
            _reject("CTP QueryTrade rows differ from the applied callback window")
        if len(window.events) not in {len(rows), len(rows) + 1}:
            _reject("CTP QueryTrade callback count differs from returned rows")
        for callback_row, query_row in zip(callback_rows, rows):
            if any(
                type(callback_row[name]) is not type(query_row[name])
                or callback_row[name] != query_row[name]
                for name in SAFE_TRADE_FIELD_NAMES
            ):
                _reject("CTP QueryTrade result differs from its applied callback row")

    def _query_complete_trade_window(
        self,
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        snapshot: CtpOwnerCallbackObservationSnapshotV1,
        binding: CtpCallbackSessionBindingV1,
        *,
        timeout: float,
        deadline_monotonic: float,
    ) -> _CtpTradeQueryWindowEvidence:
        if time.monotonic() >= deadline_monotonic:
            _reject("CTP zero-fill issue deadline expired before QueryTrade")
        trader = self._owner._trader
        try:
            query_scope = _scope_for_client(trader)
        except CtpNativeQueryCertificateError as error:
            _reject("CTP QueryTrade session scope is not trusted", cause=error)
        if (
            not query_scope.read_only_ready
            or query_scope.connection_generation != binding.connection_generation
            or query_scope.trading_day != binding.trading_day
        ):
            _reject("CTP QueryTrade session differs from the active Store owner")
        fence = self._store.begin_ctp_order_target_query(
            self._scope,
            intent.intent_id,
            owner_handle=self._owner_handle,
            writer_lease=self._writer_lease,
        )
        self._query._validate_fence(
            fence, self._owner, intent, reservation, binding
        )
        if time.monotonic() >= deadline_monotonic:
            _reject("CTP zero-fill issue deadline expired before QueryTrade submission")
        result = trader.query_trades_result(
            instrument_id=intent.instrument,
            exchange_id=snapshot.exchange_id,
            timeout=float(timeout),
            deadline_monotonic=deadline_monotonic,
        )
        self._owner.pump_callbacks(256)
        self._assert_thread_and_owner()
        if time.monotonic() >= deadline_monotonic:
            _reject("CTP zero-fill issue deadline expired before QueryTrade validation")
        if type(result) is not QueryResult:
            _reject("exact typed CTP QueryTrade result is required")
        try:
            _query_result_shape(result)
            current_scope = _scope_for_client(trader)
            if not _same_scope(query_scope, current_scope):
                _reject("CTP QueryTrade session changed during request")
            source = _source_for_result(
                result,
                current_scope,
                now_utc=datetime.now(timezone.utc),
                now_monotonic=time.monotonic(),
            )
        except CtpNativeQueryCertificateError as error:
            _reject("CTP QueryTrade result source is not trusted", cause=error)
        expected_filters = tuple(
            sorted(
                {
                    "BrokerID": current_scope.broker_id,
                    "ExchangeID": snapshot.exchange_id,
                    "InstrumentID": intent.instrument,
                    "InvestorID": current_scope.investor_id,
                    "TradeID": "",
                    "TradeTimeEnd": "",
                    "TradeTimeStart": "",
                }.items()
            )
        )
        now_monotonic = time.monotonic()
        if (
            type(result) is not QueryResult
            or result.request_type != "trades"
            or type(result.request_id) is not int
            or not 0 < result.request_id <= 2_147_483_647
            or result.connection_generation != binding.connection_generation
            or result.account_fingerprint != current_scope.account_fingerprint
            or result.complete is not True
            or result.is_last_seen is not True
            or result.timed_out is not False
            or result.unsupported is not False
            or result.submit_code != 0
            or result.error_code not in (None, 0)
            or result.error_message != ""
            or result.late_callback_count != 0
            or type(result.records) is not tuple
            or len(result.records) > _MAX_QUERY_TRADE_RECORDS
            or source.request_intent_filters != expected_filters
            or source.request_filters != expected_filters
            or source.request_intent_parameters != ()
            or source.request_parameters != ()
            or source.explicit_request_filters != ()
            or source.started_monotonic < fence.created_at_ns / 1_000_000_000
            or source.completed_monotonic > now_monotonic
            or source.completed_monotonic * 1_000_000_000 > fence.expires_at_ns
            or source.trading_day != query_scope.trading_day
            or source.broker_id != query_scope.broker_id
            or source.investor_id != query_scope.investor_id
        ):
            _reject("CTP QueryTrade completion, bounds, or exact filters are invalid")
        try:
            records_sha256 = _query_records_digest(result.records)
        except (TypeError, ValueError) as error:
            _reject("CTP QueryTrade result rows are not canonical", cause=error)
        if records_sha256 != source.records_sha256:
            _reject("CTP QueryTrade result rows differ from terminal source digest")

        rows = tuple(
            self._trade_row(record, current_scope, intent, snapshot.exchange_id)
            for record in result.records
        )
        trade_ids: set[tuple[str, str, str]] = set()
        target_trade_count = 0
        for row in rows:
            key = (row["TradingDay"], row["ExchangeID"], row["TradeID"])
            if key in trade_ids:
                _reject("CTP QueryTrade result contains a duplicate trade identity")
            trade_ids.add(key)
            order_ref_match = row["OrderRef"] == reservation.order_ref
            order_sys_id_match = row["OrderSysID"] == snapshot.order_sys_id
            if order_ref_match or order_sys_id_match:
                if not (order_ref_match and order_sys_id_match):
                    _reject("CTP QueryTrade target order correlation is ambiguous")
                target_trade_count += 1

        window = self._store.read_ctp_order_target_trade_query_window(
            self._scope,
            fence,
            query_request_id=result.request_id,
            owner_handle=self._owner_handle,
            writer_lease=self._writer_lease,
        )
        self._validate_trade_query_window(window, fence, result, rows)
        if target_trade_count != 0:
            _reject("CTP QueryTrade proves the zero-fill order already has a trade")
        filters_sha256 = payload_sha256(
            [[name, value] for name, value in source.request_filters]
        )
        return _CtpTradeQueryWindowEvidence(
            query_scope=query_scope,
            fence=fence,
            window=window,
            result=result,
            source=source,
            records_sha256=records_sha256,
            filters_sha256=filters_sha256,
            target_trade_count=target_trade_count,
            intent_instrument_id=intent.instrument,
            target_order_sys_id=snapshot.order_sys_id,
        )

    def _readback_trade_query(
        self,
        receipt: _CtpTradeQueryWindowEvidence,
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        snapshot: CtpOwnerCallbackObservationSnapshotV1,
        binding: CtpCallbackSessionBindingV1,
    ) -> None:
        try:
            current_scope = _scope_for_client(self._owner._trader)
            if not _same_scope(receipt.query_scope, current_scope):
                _reject("CTP QueryTrade session changed after evidence issuance")
            source = _source_for_result(
                receipt.result,
                current_scope,
                now_utc=datetime.now(timezone.utc),
                now_monotonic=time.monotonic(),
            )
            digest = _query_records_digest(receipt.result.records)
        except CtpNativeQueryCertificateError as error:
            _reject("CTP QueryTrade result failed source readback", cause=error)
        if (
            source is not receipt.source
            or digest != receipt.records_sha256
            or receipt.result.connection_generation != binding.connection_generation
            or receipt.fence.owner_handle is not self._owner_handle
            or receipt.fence.managed_intent_id != intent.intent_id
            or receipt.fence.order_ref != reservation.order_ref
            or receipt.fence.session_binding_sha256 != binding.session_binding_sha256
            or receipt.target_trade_count != 0
        ):
            _reject("CTP QueryTrade evidence changed after issuance")
        window = self._store.read_ctp_order_target_trade_query_window(
            self._scope,
            receipt.fence,
            query_request_id=receipt.result.request_id,
            owner_handle=self._owner_handle,
            writer_lease=self._writer_lease,
        )
        if window is not receipt.window:
            _reject("CTP QueryTrade Store window changed after issuance")
        rows = tuple(
            self._trade_row(
                record,
                current_scope,
                intent,
                snapshot.exchange_id,
            )
            for record in receipt.result.records
        )
        if any(
            row["OrderRef"] == reservation.order_ref
            or row["OrderSysID"] == snapshot.order_sys_id
            for row in rows
        ):
            _reject("CTP QueryTrade target order changed after issuance")
        self._validate_trade_query_window(window, receipt.fence, receipt.result, rows)

    def issue(
        self, managed_intent_id: str, *, timeout: float = 5.0
    ) -> CtpZeroFillTrackedOrderEvidenceV1 | CtpOwnerCallbackObservationNotReadyV1:
        """Query and issue evidence for one exact durable managed intent."""

        if threading.current_thread() is not self._thread:
            _reject("CTP zero-fill reader is confined to its owner thread")
        if self._busy or self._issued is not None:
            _reject("CTP zero-fill reader is busy or already issued evidence")
        if (
            type(timeout) not in (int, float)
            or isinstance(timeout, bool)
            or not isfinite(timeout)
            or timeout <= 0
            or timeout > _MAX_ISSUE_TIMEOUT_SECONDS
        ):
            _reject("CTP zero-fill issue timeout is outside the bounded range")
        issue_deadline = time.monotonic() + float(timeout)
        self._busy = True
        try:
            binding = self._assert_thread_and_owner()
            if time.monotonic() >= issue_deadline:
                _reject("CTP zero-fill issue deadline expired during owner preflight")
            intent, reservation, command, dispatch_binding = self._read_target(
                managed_intent_id, binding
            )
            if time.monotonic() >= issue_deadline:
                _reject("CTP zero-fill issue deadline expired during target readback")
            snapshot, observation = self._read_source(
                intent, reservation, command, dispatch_binding, binding
            )
            if time.monotonic() >= issue_deadline:
                _reject("CTP zero-fill issue deadline expired during callback readback")
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
                deadline_monotonic=issue_deadline,
            )
            if time.monotonic() >= issue_deadline:
                _reject("CTP zero-fill issue deadline expired after QueryOrder")
            projection = self._validate_projection(
                handle, intent, reservation, snapshot, binding
            )
            trade_query = self._query_complete_trade_window(
                intent,
                reservation,
                snapshot,
                binding,
                timeout=timeout,
                deadline_monotonic=issue_deadline,
            )
            if time.monotonic() >= issue_deadline:
                _reject("CTP zero-fill issue deadline expired after QueryTrade")

            current_binding = self._assert_thread_and_owner()
            current_target = self._read_target(intent.intent_id, current_binding)
            if (
                current_binding != binding
                or current_target[0] != intent
                or current_target[1] != reservation
                or current_target[2] != command
                or current_target[3] != dispatch_binding
            ):
                _reject("CTP managed OPEN target changed during its native queries")
            current_snapshot, current_observation = self._read_source(
                intent, reservation, command, dispatch_binding, current_binding
            )
            if (
                type(current_snapshot) is not CtpOwnerCallbackObservationSnapshotV1
                or current_snapshot != snapshot
                or type(current_observation) is not ProviderObservation
                or current_observation != observation
            ):
                _reject("CTP callback source changed during its native queries")
            if time.monotonic() >= issue_deadline:
                _reject("CTP zero-fill issue deadline expired during final source readback")
            if self._store.read_ctp_order_target_projection(self._scope, handle) != projection:
                _reject("CTP query projection failed same-Store readback")

            verified_at_ns = time.monotonic_ns()
            expires_at_ns = min(
                verified_at_ns + _MAX_EVIDENCE_TTL_NS,
                projection.expires_at_ns,
                int(issue_deadline * 1_000_000_000),
            )
            if verified_at_ns >= expires_at_ns or time.monotonic() >= issue_deadline:
                _reject("CTP zero-fill issue deadline expired before evidence issuance")
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
                "trade_query": trade_query,
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
            if time.monotonic() >= issue_deadline:
                _reject("CTP zero-fill issue deadline expired during evidence construction")
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
                trade_query=trade_query,
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
            if attempt.trade_query is not evidence.trade_query:
                _reject("CTP QueryTrade evidence identity changed after issuance")
            self._readback_trade_query(
                attempt.trade_query,
                attempt.intent,
                attempt.reservation,
                attempt.callback_snapshot,
                binding,
            )
            if time.monotonic_ns() >= evidence.expires_at_ns:
                _reject("CTP zero-fill evidence expired during readback")
            return evidence
        finally:
            self._busy = False


__all__ = [
    "CtpZeroFillTrackedOrderEvidenceV1",
    "CtpZeroFillTrackedOrderReader",
]
