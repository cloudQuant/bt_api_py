"""Source-bound evidence for an exact terminal, zero-fill CTP order query.

This adapter borrows one already-active :class:`CtpExecutionOwner`.  It does
not change the existing OPEN/PARTIAL cancel-target projection and grants no
dispatch or Risk authority.  A returned value is useful only while the same
reader can re-read its exact QueryResult and Store evidence with ``readback``.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any, ClassVar

from bt_api_ctp.query import QueryResult
from bt_api_execution import (
    ContractValidationError,
    CtpCallbackSessionBindingV1,
    CtpOrderIdentityReservation,
    CtpOrderTargetQueryFence,
    CtpOrderTargetQueryWindow,
    CtpOwnerCallbackObservationNotReadyV1,
    CtpOwnerCallbackObservationSnapshotV1,
    ExecutionScope,
    OrderIntent,
    payload_sha256,
)
from bt_api_execution.ctp_single_worker_candidate import stable_managed_command_id
from bt_api_py.ctp_execution_owner import CtpExecutionOwner
from bt_api_ctp.containers.ctp.ctp_native_query_certificate import (
    _same_scope,
    _scope_for_client,
)
from bt_api_py.ctp_order_target_query import (
    CtpOrderTargetQueryEvaluator,
    _SAFE_ORDER_FIELDS,
    _ctp_text,
    _intent_native_payload,
    _reservation_payload,
    _source_tags_payload,
    _exact_int,
)
from bt_api_py.ctp_service_execution import encode_ctp_provider_order_id


_VERIFIER_ID = "bt_api_py.ctp_terminal_order_query.v1"
_MAX_TIMEOUT_SECONDS = 20.0
_MAX_EVIDENCE_TTL_NS = 5_000_000_000
_TERMINAL_ORDER_STATES = frozenset({"CANCELLED", "REJECTED"})
_CANCEL_ACTION_STATES = frozenset({"ACKNOWLEDGED", "REJECTED", "TERMINAL"})
_DIGEST_FIELDS = frozenset(
    {
        "terminal_ingress_record_digest_sha256",
        "terminal_source_digest_sha256",
        "query_filter_sha256",
        "query_records_sha256",
        "query_source_sha256",
        "query_window_sha256",
        "reservation_digest_sha256",
        "session_binding_sha256",
        "query_account_fingerprint_sha256",
        "evidence_sha256",
    }
)


def _monotonic_ns() -> int:
    return time.monotonic_ns()


def _reject(message: str, *, cause: BaseException | None = None) -> None:
    error = ContractValidationError(message)
    if cause is None:
        raise error
    raise error from cause


def _digest_text(value: object, name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.lower()
        or any(char not in "0123456789abcdef" for char in value)
    ):
        _reject("invalid CTP terminal-query " + name)


@dataclass(frozen=True, slots=True)
class CtpTerminalOrderQueryNotReadyV1:
    """A finite reason why an exact zero-fill terminal query is unavailable."""

    reason_code: str

    _ALLOWED: ClassVar[frozenset[str]] = frozenset(
        {"CALLBACK_SOURCE_NOT_READY", "ORDER_NOT_TERMINAL", "ORDER_HAS_FILL"}
    )

    def __post_init__(self) -> None:
        if type(self.reason_code) is not str or self.reason_code not in self._ALLOWED:
            raise ContractValidationError("invalid CTP terminal-query not-ready reason")


@dataclass(frozen=True, slots=True)
class CtpTerminalOrderQueryEvidenceV1:
    """A short-lived, source-bound observation of one terminal zero-fill order.

    ``to_payload`` is an audit representation only.  Callers must ask the
    issuing reader to ``readback`` this exact object before treating it as
    evidence; its hash does not authenticate a reconstructed value.
    """

    terminal_state: str
    account_key: str
    scope_key: str
    trading_day: str
    managed_intent_id: str
    runtime_order_id: str
    order_ref: str
    submit_command_id: str
    provider_order_id: str
    instrument_id: str
    exchange_id: str
    order_sys_id: str
    callback_owner_intent_id: str
    session_generation_id: str
    callback_stream_id: str
    session_binding_sha256: str
    order_source_sequence: int
    terminal_ingress_record_digest_sha256: str
    terminal_source_digest_sha256: str
    request_id: int
    front_id: int
    session_id: int
    quantity: int
    volume_traded: int
    filled_quantity: int
    trade_fact_count: int
    trade_notional: str
    commission_quality: str
    cancel_command_id: str | None
    cancel_action_state: str | None
    reservation_digest_sha256: str
    query_fence_id: str
    query_start_source_sequence: int
    query_end_source_sequence: int
    query_applied_source_sequence: int
    query_request_id: int
    query_filter_sha256: str
    query_records_sha256: str
    query_source_sha256: str
    query_window_sha256: str
    query_callback_ingress_digests: tuple[str, ...]
    query_account_fingerprint_sha256: str
    query_connection_generation: int
    query_started_at_utc: str
    query_completed_at_utc: str
    query_started_monotonic: float
    query_completed_monotonic: float
    verified_at_ns: int
    expires_at_ns: int
    evidence_sha256: str

    schema: ClassVar[str] = "ctp_terminal_order_query_evidence.v1"

    def __post_init__(self) -> None:
        for name in (
            "account_key",
            "scope_key",
            "managed_intent_id",
            "runtime_order_id",
            "submit_command_id",
            "provider_order_id",
            "instrument_id",
            "exchange_id",
            "order_sys_id",
            "callback_owner_intent_id",
            "session_generation_id",
            "callback_stream_id",
        ):
            if type(getattr(self, name)) is not str or not getattr(self, name):
                raise ContractValidationError("invalid CTP terminal-query " + name)
        if (
            type(self.terminal_state) is not str
            or self.terminal_state not in _TERMINAL_ORDER_STATES
            or type(self.trading_day) is not str
            or len(self.trading_day) != 8
            or not self.trading_day.isascii()
            or not self.trading_day.isdigit()
            or type(self.order_ref) is not str
            or len(self.order_ref) != 12
            or not self.order_ref.isascii()
            or not self.order_ref.isdigit()
        ):
            raise ContractValidationError("invalid CTP terminal-query identity")
        for name in _DIGEST_FIELDS:
            if name != "evidence_sha256":
                _digest_text(getattr(self, name), name)
        _digest_text(self.evidence_sha256, "evidence digest")
        for name in (
            "order_source_sequence",
            "request_id",
            "front_id",
            "session_id",
            "quantity",
            "query_start_source_sequence",
            "query_end_source_sequence",
            "query_applied_source_sequence",
            "query_request_id",
            "query_connection_generation",
            "verified_at_ns",
            "expires_at_ns",
        ):
            minimum = 0 if name in {"query_start_source_sequence", "query_connection_generation"} else 1
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ContractValidationError("invalid CTP terminal-query " + name)
        if (
            type(self.volume_traded) is not int
            or self.volume_traded != 0
            or type(self.filled_quantity) is not int
            or self.filled_quantity != 0
            or type(self.trade_fact_count) is not int
            or self.trade_fact_count != 0
            or type(self.trade_notional) is not str
            or self.trade_notional != "0"
            or self.commission_quality != "INCOMPLETE"
            or self.query_end_source_sequence <= self.query_start_source_sequence
            or self.query_applied_source_sequence != self.query_end_source_sequence
            or self.expires_at_ns <= self.verified_at_ns
            or self.expires_at_ns - self.verified_at_ns > _MAX_EVIDENCE_TTL_NS
            or type(self.query_fence_id) is not str
            or not self.query_fence_id
            or type(self.query_callback_ingress_digests) is not tuple
            or len(self.query_callback_ingress_digests) != 2
            or any(type(item) is not str or len(item) != 64 for item in self.query_callback_ingress_digests)
        ):
            raise ContractValidationError("invalid CTP terminal-query source window")
        for name in self.query_callback_ingress_digests:
            _digest_text(name, "query callback ingress digest")
        if self.provider_order_id != encode_ctp_provider_order_id(self.exchange_id, self.order_sys_id):
            raise ContractValidationError("CTP provider order ID differs from its native identity")
        if (
            type(self.query_started_at_utc) is not str
            or type(self.query_completed_at_utc) is not str
        ):
            raise ContractValidationError("invalid CTP terminal-query UTC timestamps")
        try:
            started_utc = datetime.fromisoformat(self.query_started_at_utc)
            completed_utc = datetime.fromisoformat(self.query_completed_at_utc)
        except (TypeError, ValueError) as error:
            raise ContractValidationError("invalid CTP terminal-query UTC timestamps") from error
        if (
            started_utc.tzinfo is None
            or started_utc.utcoffset() is None
            or completed_utc.tzinfo is None
            or completed_utc.utcoffset() is None
            or completed_utc < started_utc
            or type(self.query_started_monotonic) not in (int, float)
            or isinstance(self.query_started_monotonic, bool)
            or not math.isfinite(self.query_started_monotonic)
            or type(self.query_completed_monotonic) not in (int, float)
            or isinstance(self.query_completed_monotonic, bool)
            or not math.isfinite(self.query_completed_monotonic)
            or self.query_completed_monotonic < self.query_started_monotonic
        ):
            raise ContractValidationError("invalid CTP terminal-query clock facts")
        if self.cancel_command_id is None:
            if self.cancel_action_state is not None:
                raise ContractValidationError("CTP terminal-query cancel state lacks command ID")
        elif (
            type(self.cancel_command_id) is not str
            or not self.cancel_command_id
            or type(self.cancel_action_state) is not str
            or self.cancel_action_state not in _CANCEL_ACTION_STATES
        ):
            raise ContractValidationError("invalid CTP terminal-query cancel binding")
        unsigned = self.to_payload()
        unsigned.pop("evidence_sha256")
        if payload_sha256(unsigned) != self.evidence_sha256:
            raise ContractValidationError("CTP terminal-query evidence digest differs")

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"schema": self.schema}
        payload.update({field.name: getattr(self, field.name) for field in fields(self)})
        return payload


@dataclass(frozen=True, slots=True)
class _TerminalAttempt:
    scope: ExecutionScope
    intent: OrderIntent
    reservation: CtpOrderIdentityReservation
    command: Any
    binding: CtpCallbackSessionBindingV1
    broker_id: str
    user_id: str
    query_scope: Any
    snapshot: CtpOwnerCallbackObservationSnapshotV1
    fence: CtpOrderTargetQueryFence
    result: QueryResult[Any]
    query_window: CtpOrderTargetQueryWindow
    source: Any
    row: dict[str, Any]
    evidence: CtpTerminalOrderQueryEvidenceV1 | None


class CtpTerminalOrderQueryReader:
    """Verify one fresh exact-order terminal query from an active CTP owner."""

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
        self._shared = CtpOrderTargetQueryEvaluator(owner)
        self._busy = False
        self._attempt: _TerminalAttempt | None = None
        self._issued: CtpTerminalOrderQueryEvidenceV1 | None = None

    def _assert_thread_and_owner(self) -> CtpCallbackSessionBindingV1:
        if threading.current_thread() is not self._thread:
            _reject("CTP terminal-query reader is confined to its creating thread")
        self._owner._assert_worker_thread()
        binding = self._owner._assert_active_owner()
        if binding != self._initial_binding:
            _reject("borrowed CTP session binding changed")
        return binding

    def _read_snapshot(
        self, submit_command_id: str, cancel_command_id: str | None
    ) -> CtpOwnerCallbackObservationSnapshotV1 | CtpTerminalOrderQueryNotReadyV1:
        snapshot = self._store.read_ctp_owner_callback_observation_snapshot(
            self._scope,
            submit_command_id,
            cancel_command_id=cancel_command_id,
        )
        if type(snapshot) is CtpOwnerCallbackObservationNotReadyV1:
            return CtpTerminalOrderQueryNotReadyV1("CALLBACK_SOURCE_NOT_READY")
        if type(snapshot) is not CtpOwnerCallbackObservationSnapshotV1:
            _reject("exact typed CTP callback snapshot is required")
        return snapshot

    def _validate_snapshot(
        self,
        snapshot: CtpOwnerCallbackObservationSnapshotV1,
        *,
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        command_id: str,
        binding: CtpCallbackSessionBindingV1,
        cancel_command_id: str | None,
    ) -> CtpTerminalOrderQueryNotReadyV1 | None:
        if (
            snapshot.account_key != self._scope.account_key
            or snapshot.scope_key != self._scope.key
            or snapshot.trading_day != self._scope.trading_day
            or snapshot.command_id != command_id
            or snapshot.owner_intent_id != binding.owner_intent_id
            or snapshot.session_generation_id != binding.session_generation_id
            or snapshot.order_ref != reservation.order_ref
            or snapshot.cancel_command_id != cancel_command_id
        ):
            _reject("CTP callback snapshot differs from the exact managed target")
        if snapshot.provider_state not in _TERMINAL_ORDER_STATES:
            return CtpTerminalOrderQueryNotReadyV1("ORDER_NOT_TERMINAL")
        if (
            snapshot.volume_traded != 0
            or snapshot.filled_quantity != 0
            or snapshot.trade_fact_count != 0
            or type(snapshot.trade_notional) is not Decimal
            or snapshot.trade_notional != Decimal(0)
            or snapshot.cumulative_commission is not None
            or snapshot.commission_quality != "INCOMPLETE"
        ):
            return CtpTerminalOrderQueryNotReadyV1("ORDER_HAS_FILL")
        if snapshot.provider_state == "CANCELLED" and cancel_command_id is not None:
            if snapshot.cancel_action_state not in {"ACKNOWLEDGED", "TERMINAL"}:
                return CtpTerminalOrderQueryNotReadyV1("CALLBACK_SOURCE_NOT_READY")
        if snapshot.provider_state == "REJECTED" and cancel_command_id is not None:
            if snapshot.cancel_action_state != "REJECTED":
                return CtpTerminalOrderQueryNotReadyV1("CALLBACK_SOURCE_NOT_READY")
        if intent.intent_id != reservation.managed_intent_id:
            _reject("CTP target intent differs from the Store reservation")
        return None

    @staticmethod
    def _validated_terminal_row(
        result: QueryResult[Any],
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        binding: CtpCallbackSessionBindingV1,
        snapshot: CtpOwnerCallbackObservationSnapshotV1,
        broker_id: str,
        user_id: str,
        exchange_id: str,
        order_sys_id: str,
    ) -> tuple[dict[str, Any], str]:
        if type(result.records) is not tuple or len(result.records) != 1:
            _reject("CTP terminal query must return exactly one row")
        row: dict[str, Any] = {}
        record = result.records[0]
        from bt_api_ctp.containers.ctp.ctp_native_query_certificate import _record_value

        for name in _SAFE_ORDER_FIELDS:
            present, value = _record_value(record, name)
            if not present:
                _reject("CTP terminal query row is missing " + name)
            row[name] = value
        expected_native = _intent_native_payload(
            intent, reservation, binding, broker_id, user_id, exchange_id
        )
        exact = {
            "BrokerID": broker_id,
            "InvestorID": user_id,
            "UserID": user_id,
            "InstrumentID": intent.instrument,
            "ExchangeID": exchange_id,
            "OrderRef": reservation.order_ref,
            "OrderSysID": order_sys_id,
            "RequestID": snapshot.request_id,
            "FrontID": snapshot.front_id,
            "SessionID": snapshot.session_id,
            "TradingDay": snapshot.trading_day,
            "Direction": expected_native["Direction"],
            "CombOffsetFlag": expected_native["CombOffsetFlag"],
            "CombHedgeFlag": "1",
            "VolumeTotalOriginal": int(intent.quantity),
            "VolumeTraded": 0,
            "VolumeTotal": int(intent.quantity),
        }
        if any(type(row[name]) is not type(value) or row[name] != value for name, value in exact.items()):
            _reject("CTP terminal query row differs from the exact durable target")
        for name in ("SequenceNo", "NotifySequence"):
            _exact_int(row[name], name)
        state = snapshot.provider_state
        if state == "CANCELLED":
            if row["OrderStatus"] != "5" or row["OrderSubmitStatus"] not in {"1", "3"}:
                _reject("CTP query row does not prove the terminal cancelled callback state")
        elif state == "REJECTED":
            if row["OrderStatus"] != "4" or row["OrderSubmitStatus"] != "4":
                _reject("CTP query row does not prove the terminal rejected callback state")
        else:
            _reject("CTP callback state is not terminal")
        price = row["LimitPrice"]
        if (
            type(price) is not float
            or not math.isfinite(price)
            or price <= 0
            or Decimal(str(price)) != intent.price
        ):
            _reject("CTP terminal query row LIMIT price differs from the durable intent")
        return row, state

    @staticmethod
    def _validate_exact_target_row(
        row: dict[str, Any],
        fence: CtpOrderTargetQueryFence,
        snapshot: CtpOwnerCallbackObservationSnapshotV1,
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
    ) -> None:
        if (
            fence.managed_intent_id != intent.intent_id
            or fence.runtime_order_id != reservation.runtime_order_id
            or fence.order_ref != reservation.order_ref
            or snapshot.order_ref != reservation.order_ref
            or snapshot.exchange_id != row["ExchangeID"]
            or snapshot.order_sys_id != row["OrderSysID"]
        ):
            _reject("Store query fence lacks a managed target")
        if (
            snapshot.order_source_sequence > fence.start_source_sequence
            or snapshot.order_source_sequence <= 0
            or row["RequestID"] != snapshot.request_id
        ):
            _reject("CTP query window does not follow the source terminal callback")

    def _query_source_payload(self, attempt: _TerminalAttempt) -> dict[str, Any]:
        source = attempt.source
        return {
            "schema": "ctp_terminal_order_query_source.v1",
            "scope": attempt.scope.to_payload(),
            "submit_command_id": attempt.command.command_id,
            "reservation": _reservation_payload(attempt.reservation),
            "session_binding_sha256": attempt.binding.session_binding_sha256,
            "source_tags": attempt.fence.source_tags,
            "terminal_callback": {
                "state": attempt.snapshot.provider_state,
                "source_sequence": attempt.snapshot.order_source_sequence,
                "ingress_record_digest_sha256": attempt.snapshot.ingress_record_digest_sha256,
                "source_digest_sha256": attempt.snapshot.source_digest_sha256,
            },
            "query": {
                "fence_id": attempt.fence.fence_id,
                "request_id": attempt.result.request_id,
                "filters": dict(source.request_filters),
                "records_sha256": source.records_sha256,
                "started_at_utc": source.started_at_utc.isoformat(),
                "completed_at_utc": source.completed_at_utc.isoformat(),
                "started_monotonic": source.started_monotonic,
                "completed_monotonic": source.completed_monotonic,
                "account_fingerprint": source.account_fingerprint,
                "connection_generation": source.connection_generation,
                "trading_day": source.trading_day,
            },
            "window": {
                "sha256": attempt.query_window.window_sha256,
                "start_source_sequence": attempt.query_window.start_source_sequence,
                "end_source_sequence": attempt.query_window.end_source_sequence,
                "applied_source_sequence": attempt.query_window.applied_source_sequence,
                "callback_ingress_digests": tuple(
                    event.record_digest_sha256 for event in attempt.query_window.events
                ),
            },
        }

    def _make_evidence(self, attempt: _TerminalAttempt, *, verified_at_ns: int) -> CtpTerminalOrderQueryEvidenceV1:
        source = attempt.source
        filters = dict(source.request_filters)
        query_filter_sha256 = payload_sha256(
            {"schema": "ctp_terminal_order_query_filters.v1", "filters": filters}
        )
        query_account_fingerprint_sha256 = sha256(
            source.account_fingerprint.encode("ascii")
        ).hexdigest()
        reservation_digest = payload_sha256(
            {
                "registration_type": "ctp_order_identity_registration.v1",
                "reservation": _reservation_payload(attempt.reservation),
                "session_binding": self._store._ctp_callback_session_binding_payload(
                    attempt.binding
                ),
            }
        )
        source_sha256 = payload_sha256(self._query_source_payload(attempt))
        expires_at_ns = min(
            verified_at_ns + _MAX_EVIDENCE_TTL_NS,
            attempt.fence.expires_at_ns,
            int(source.trusted_expires_monotonic * 1_000_000_000),
        )
        if expires_at_ns <= verified_at_ns:
            _reject("CTP terminal-query source expired before evidence issuance")
        base = {
            "terminal_state": attempt.snapshot.provider_state,
            "account_key": attempt.scope.account_key,
            "scope_key": attempt.scope.key,
            "trading_day": attempt.scope.trading_day or "",
            "managed_intent_id": attempt.intent.intent_id,
            "runtime_order_id": attempt.reservation.runtime_order_id,
            "order_ref": attempt.reservation.order_ref,
            "submit_command_id": attempt.command.command_id,
            "provider_order_id": encode_ctp_provider_order_id(
                attempt.snapshot.exchange_id, attempt.snapshot.order_sys_id
            ),
            "instrument_id": attempt.intent.instrument,
            "exchange_id": attempt.snapshot.exchange_id,
            "order_sys_id": attempt.snapshot.order_sys_id,
            "callback_owner_intent_id": attempt.snapshot.owner_intent_id,
            "session_generation_id": attempt.snapshot.session_generation_id,
            "callback_stream_id": attempt.snapshot.callback_stream_id,
            "session_binding_sha256": attempt.binding.session_binding_sha256,
            "order_source_sequence": attempt.snapshot.order_source_sequence,
            "terminal_ingress_record_digest_sha256": attempt.snapshot.ingress_record_digest_sha256,
            "terminal_source_digest_sha256": attempt.snapshot.source_digest_sha256,
            "request_id": attempt.snapshot.request_id,
            "front_id": attempt.snapshot.front_id,
            "session_id": attempt.snapshot.session_id,
            "quantity": int(attempt.intent.quantity),
            "volume_traded": 0,
            "filled_quantity": 0,
            "trade_fact_count": 0,
            "trade_notional": "0",
            "commission_quality": "INCOMPLETE",
            "cancel_command_id": attempt.snapshot.cancel_command_id,
            "cancel_action_state": attempt.snapshot.cancel_action_state,
            "reservation_digest_sha256": reservation_digest,
            "query_fence_id": attempt.fence.fence_id,
            "query_start_source_sequence": attempt.query_window.start_source_sequence,
            "query_end_source_sequence": attempt.query_window.end_source_sequence,
            "query_applied_source_sequence": attempt.query_window.applied_source_sequence,
            "query_request_id": attempt.result.request_id,
            "query_filter_sha256": query_filter_sha256,
            "query_records_sha256": source.records_sha256,
            "query_source_sha256": source_sha256,
            "query_window_sha256": attempt.query_window.window_sha256,
            "query_callback_ingress_digests": tuple(
                event.record_digest_sha256 for event in attempt.query_window.events
            ),
            "query_account_fingerprint_sha256": query_account_fingerprint_sha256,
            "query_connection_generation": source.connection_generation,
            "query_started_at_utc": source.started_at_utc.isoformat(),
            "query_completed_at_utc": source.completed_at_utc.isoformat(),
            "query_started_monotonic": source.started_monotonic,
            "query_completed_monotonic": source.completed_monotonic,
            "verified_at_ns": verified_at_ns,
            "expires_at_ns": expires_at_ns,
        }
        evidence_sha256 = payload_sha256({"schema": CtpTerminalOrderQueryEvidenceV1.schema, **base})
        return CtpTerminalOrderQueryEvidenceV1(
            **base,
            evidence_sha256=evidence_sha256,
        )

    def query_terminal_order(
        self,
        managed_intent_id: str,
        *,
        cancel_command_id: str | None = None,
        timeout: float = 5.0,
    ) -> CtpTerminalOrderQueryEvidenceV1 | CtpTerminalOrderQueryNotReadyV1:
        """Issue one exact filtered QueryOrder and verify terminal zero-fill evidence.

        The order identifiers are derived from the immutable callback snapshot;
        callers cannot broaden or retarget the native query.  No retries,
        projection writes, or execution/Risk mutations are performed.
        """

        if threading.current_thread() is not self._thread:
            _reject("CTP terminal-query reader is confined to its creating thread")
        if self._busy:
            _reject("CTP terminal-query reader does not permit reentry")
        if (
            type(timeout) not in (int, float)
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > _MAX_TIMEOUT_SECONDS
        ):
            _reject("CTP terminal-query timeout is outside the bounded range")
        self._busy = True
        self._attempt = None
        self._issued = None
        try:
            binding = self._assert_thread_and_owner()
            if cancel_command_id is not None:
                _ctp_text(cancel_command_id, "cancel command ID", 128)
            _ctp_text(managed_intent_id, "managed intent ID", 128)
            reservation = self._store.read_ctp_order_identity(self._scope, managed_intent_id)
            if type(reservation) is not CtpOrderIdentityReservation:
                _reject("durable CTP OrderRef reservation is required")
            submit_command_id = stable_managed_command_id(
                "submit", managed_intent_id, reservation.runtime_order_id
            )
            initial = self._read_snapshot(submit_command_id, cancel_command_id)
            if type(initial) is CtpTerminalOrderQueryNotReadyV1:
                return initial
            intent = self._store.get_intent(managed_intent_id, scope=self._scope)
            if type(intent) is not OrderIntent:
                _reject("durable CTP order intent is required")
            not_ready = self._validate_snapshot(
                initial,
                intent=intent,
                reservation=reservation,
                command_id=submit_command_id,
                binding=binding,
                cancel_command_id=cancel_command_id,
            )
            if not_ready is not None:
                return not_ready
            (
                shared_intent,
                shared_reservation,
                command,
                binding,
                broker_id,
                user_id,
                query_scope,
                filters,
            ) = self._shared._read_preflight(
                managed_intent_id,
                instrument_id=intent.instrument,
                exchange_id=initial.exchange_id,
                order_sys_id=initial.order_sys_id,
            )
            if shared_intent != intent or shared_reservation != reservation:
                _reject("CTP shared query preflight changed the exact target")
            fence = self._store.begin_ctp_order_target_query(
                self._scope,
                managed_intent_id,
                owner_handle=self._owner_handle,
                writer_lease=self._writer_lease,
            )
            self._shared._validate_fence(fence, self._owner, intent, reservation, binding)
            if fence.start_source_sequence < initial.order_source_sequence:
                _reject("CTP terminal callback is newer than the query fence")
            result = self._owner._trader.query_orders_result(
                instrument_id=filters["instrument_id"],
                exchange_id=filters["exchange_id"],
                order_sys_id=filters["order_sys_id"],
                timeout=float(timeout),
            )
            self._owner.pump_callbacks(256)
            self._assert_thread_and_owner()
            now_mono = time.monotonic()
            source = self._shared._validate_query_source(
                result,
                query_scope,
                fence,
                broker_id=broker_id,
                user_id=user_id,
                instrument_id=filters["instrument_id"],
                exchange_id=filters["exchange_id"],
                order_sys_id=filters["order_sys_id"],
                now_monotonic=now_mono,
                now_utc=datetime.now(timezone.utc),
            )
            row, state = self._validated_terminal_row(
                result,
                intent,
                reservation,
                binding,
                initial,
                broker_id,
                user_id,
                filters["exchange_id"],
                filters["order_sys_id"],
            )
            if state != initial.provider_state:
                _reject("CTP query terminal state differs from the source callback snapshot")
            if source.completed_monotonic > now_mono:
                _reject("CTP terminal-query completion time is in the future")
            window = self._store.read_ctp_order_target_query_window(
                self._scope,
                fence,
                query_request_id=result.request_id,
                owner_handle=self._owner_handle,
                writer_lease=self._writer_lease,
            )
            self._shared._validate_window_row(window, fence, result, row)
            self._validate_exact_target_row(row, fence, initial, intent, reservation)
            final = self._read_snapshot(submit_command_id, cancel_command_id)
            if type(final) is CtpTerminalOrderQueryNotReadyV1:
                return final
            if final != initial:
                _reject("CTP terminal callback/trade snapshot changed during the query")
            placeholder = _TerminalAttempt(
                scope=self._scope,
                intent=intent,
                reservation=reservation,
                command=command,
                binding=binding,
                broker_id=broker_id,
                user_id=user_id,
                query_scope=query_scope,
                snapshot=initial,
                fence=fence,
                result=result,
                query_window=window,
                source=source,
                row=row,
                evidence=None,
            )
            evidence = self._make_evidence(placeholder, verified_at_ns=_monotonic_ns())
            self._attempt = _TerminalAttempt(
                scope=placeholder.scope,
                intent=placeholder.intent,
                reservation=placeholder.reservation,
                command=placeholder.command,
                binding=placeholder.binding,
                broker_id=placeholder.broker_id,
                user_id=placeholder.user_id,
                query_scope=placeholder.query_scope,
                snapshot=placeholder.snapshot,
                fence=placeholder.fence,
                result=placeholder.result,
                query_window=placeholder.query_window,
                source=placeholder.source,
                row=placeholder.row,
                evidence=evidence,
            )
            self._issued = evidence
            return evidence
        finally:
            self._busy = False

    def readback(
        self, evidence: CtpTerminalOrderQueryEvidenceV1
    ) -> CtpTerminalOrderQueryEvidenceV1:
        """Revalidate the exact live evidence object minted by this reader."""

        attempt = self._attempt
        if (
            threading.current_thread() is not self._thread
            or self._busy
            or type(evidence) is not CtpTerminalOrderQueryEvidenceV1
            or attempt is None
            or self._issued is not evidence
            or attempt.evidence is not evidence
            or evidence.expires_at_ns <= evidence.verified_at_ns
            or _monotonic_ns() >= evidence.expires_at_ns
        ):
            _reject("exact unexpired CTP terminal-query evidence is required")
        self._busy = True
        try:
            binding = self._assert_thread_and_owner()
            if binding != attempt.binding:
                _reject("CTP session binding changed after terminal query")
            current_scope = _scope_for_client(self._owner._trader)
            if not _same_scope(attempt.query_scope, current_scope):
                _reject("TraderClient query session changed after terminal query")
            current = self._owner._trader.get_query_result(attempt.result.request_id)
            if type(current) is not QueryResult:
                _reject("current CTP terminal QueryResult is unavailable")
            now_mono = time.monotonic()
            current_source = self._shared._validate_query_source(
                current,
                current_scope,
                attempt.fence,
                broker_id=attempt.broker_id,
                user_id=attempt.user_id,
                instrument_id=attempt.intent.instrument,
                exchange_id=attempt.snapshot.exchange_id,
                order_sys_id=attempt.snapshot.order_sys_id,
                now_monotonic=now_mono,
                now_utc=datetime.now(timezone.utc),
            )
            original_source = self._shared._validate_query_source(
                attempt.result,
                attempt.query_scope,
                attempt.fence,
                broker_id=attempt.broker_id,
                user_id=attempt.user_id,
                instrument_id=attempt.intent.instrument,
                exchange_id=attempt.snapshot.exchange_id,
                order_sys_id=attempt.snapshot.order_sys_id,
                now_monotonic=now_mono,
                now_utc=datetime.now(timezone.utc),
            )
            if (
                current.request_id != attempt.result.request_id
                or current.records != attempt.result.records
                or current_source != original_source
                or current_source.records_sha256 != attempt.source.records_sha256
            ):
                _reject("current CTP terminal query result changed")
            row, state = self._validated_terminal_row(
                current,
                attempt.intent,
                attempt.reservation,
                binding,
                attempt.snapshot,
                attempt.broker_id,
                attempt.user_id,
                attempt.snapshot.exchange_id,
                attempt.snapshot.order_sys_id,
            )
            if state != attempt.snapshot.provider_state:
                _reject("current CTP terminal query state changed")
            current_window = self._store.read_ctp_order_target_query_window(
                self._scope,
                attempt.fence,
                query_request_id=current.request_id,
                owner_handle=self._owner_handle,
                writer_lease=self._writer_lease,
            )
            if current_window != attempt.query_window:
                _reject("durable CTP terminal query window changed")
            self._shared._validate_window_row(current_window, attempt.fence, current, row)
            self._validate_exact_target_row(
                row,
                attempt.fence,
                attempt.snapshot,
                attempt.intent,
                attempt.reservation,
            )
            snapshot = self._read_snapshot(attempt.command.command_id, evidence.cancel_command_id)
            if type(snapshot) is not CtpOwnerCallbackObservationSnapshotV1 or snapshot != attempt.snapshot:
                _reject("current CTP callback/trade snapshot changed after terminal query")
            rebuilt = self._make_evidence(attempt, verified_at_ns=evidence.verified_at_ns)
            if rebuilt != evidence:
                _reject("CTP terminal-query evidence no longer matches its source")
            if _monotonic_ns() >= evidence.expires_at_ns:
                _reject("CTP terminal-query evidence expired during readback")
            return evidence
        finally:
            self._busy = False


__all__ = [
    "CtpTerminalOrderQueryEvidenceV1",
    "CtpTerminalOrderQueryNotReadyV1",
    "CtpTerminalOrderQueryReader",
]
