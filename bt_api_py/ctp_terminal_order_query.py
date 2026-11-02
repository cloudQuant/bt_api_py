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
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

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
from bt_api_ctp.managed_dispatch_contracts import stable_managed_command_id
from bt_api_py.ctp_execution_owner import CtpExecutionOwner
from bt_api_ctp.containers.ctp.ctp_native_query_certificate import (
    _same_scope,
    _scope_for_client,
)
from bt_api_py.ctp_order_target_query import (
    CtpOrderTargetQueryEvaluator,
    _reservation_payload,
    _source_tags_payload,
)
from bt_api_ctp.managed_order_query_projection import (
    _ctp_text,
    validate_terminal_order_query_row,
)
from bt_api_ctp.managed_order_identifier import encode_ctp_provider_order_id
from bt_api_ctp.terminal_order_query_contract import (
    CtpTerminalOrderQueryEvidenceV1,
    CtpTerminalOrderQueryNotReadyV1,
    _MAX_EVIDENCE_TTL_NS,
    _TERMINAL_ORDER_STATES,
)


_VERIFIER_ID = "bt_api_py.ctp_terminal_order_query.v1"
_MAX_TIMEOUT_SECONDS = 20.0


def _monotonic_ns() -> int:
    return time.monotonic_ns()


def _reject(message: str, *, cause: BaseException | None = None) -> None:
    error = ContractValidationError(message)
    if cause is None:
        raise error
    raise error from cause








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
        result: QueryResult[Any], intent: OrderIntent, reservation: CtpOrderIdentityReservation,
        binding: CtpCallbackSessionBindingV1, snapshot: CtpOwnerCallbackObservationSnapshotV1,
        broker_id: str, user_id: str, exchange_id: str, order_sys_id: str,
    ) -> tuple[dict[str, Any], str]:
        return validate_terminal_order_query_row(
            result, intent, reservation, binding, snapshot, broker_id, user_id,
            exchange_id, order_sys_id,
        )

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
