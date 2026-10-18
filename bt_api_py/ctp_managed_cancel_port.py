"""Claim-aware CTP cancellation adapter for the managed execution facade.

This port borrows one already-active :class:`CtpExecutionOwner`.  It does not
create, reconnect, close, or otherwise manage the native session.  A cancel is
dispatched only after the facade has supplied the exact one-use Risk claim;
provider state is returned only from the owner's persisted callback projection.
"""

from __future__ import annotations

import math
import queue
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from threading import current_thread
from typing import Any, Callable

from bt_api_execution import (
    CancelDispatchClaimReceiptV1,
    CancelIntent,
    CancelObservation,
    CancelRecord,
    ContractValidationError,
    CtpCallbackSessionStoreAdapter,
    CtpDispatchCommand,
    CtpOrderIdentityReservation,
    CtpOrderTargetProjectionHandle,
    CtpOwnerCallbackObservationNotReadyV1,
    ExecutionScope,
    ExecutionState,
    InvalidStateTransition,
    OrderIntent,
    SqliteExecutionStore,
    WriterLease,
)
from bt_api_execution.ctp_single_worker_candidate import (
    CtpManagedDispatchBinding,
    CtpManagedPreparedDispatch,
    stable_managed_command_id,
)
from bt_api_py.ctp_cancel_authority import CtpCancelAuthorityIssuerV1
from bt_api_py.ctp_execution_owner import CtpExecutionDispatchResult, CtpExecutionOwner
from bt_api_py.ctp_order_target_query import CtpOrderTargetQueryEvaluator
from bt_api_py.ctp_service_execution import (
    CtpOwnerCallbackReconciliationPort,
    decode_ctp_provider_order_id,
    encode_ctp_provider_order_id,
)
from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate


@dataclass(frozen=True, slots=True)
class _QueuedCancel:
    prepared: CtpManagedPreparedDispatch
    target_projection: CtpOrderTargetProjectionHandle


@dataclass(slots=True)
class _PreparedCancelTarget:
    intent: CancelIntent
    target_projection: CtpOrderTargetProjectionHandle
    submit_binding: CtpManagedDispatchBinding
    authorization_facts: "CtpPreparedCancelAuthorizationFactsV1"
    claim_consumed: bool = False
    dispatch_started: bool = False


@dataclass(frozen=True, slots=True)
class CtpPreparedCancelAuthorizationFactsV1:
    """Value-only projection of the already verified CancelPreparation."""

    cancel_id: str
    target_intent_id: str
    provider_order_id: str
    account_key: str
    scope_key: str
    trading_day: str
    runtime_order_id: str
    order_ref: str
    session_generation_id: str
    instrument_id: str
    exchange_id: str

    def __post_init__(self) -> None:
        for name in (
            "cancel_id",
            "target_intent_id",
            "provider_order_id",
            "account_key",
            "scope_key",
            "trading_day",
            "runtime_order_id",
            "order_ref",
            "session_generation_id",
            "instrument_id",
            "exchange_id",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ContractValidationError("invalid prepared CTP cancel facts")


class CtpManagedCancelNotReady(InvalidStateTransition):
    """The durable callback snapshot does not yet prove a cancel outcome."""

    def __init__(self, reason_code: str) -> None:
        if (
            type(reason_code) is not str
            or not reason_code
            or reason_code != reason_code.strip()
            or len(reason_code) > 64
            or not reason_code.isascii()
            or any(not (char.isalnum() or char in "_-") for char in reason_code)
        ):
            reason_code = "cancel_callback_not_ready"
        self.reason_code = reason_code
        super().__init__("CTP cancellation callback is not ready: " + reason_code)


class CtpManagedCancelPort:
    """Bridge one exact Risk cancellation claim to one borrowed CTP owner.

    ``prepare_target`` is intended for ``ManagedCancellationFacade``'s
    pre-dispatch hook.  It obtains a fresh same-Store target projection before
    Risk consumes the single-use claim.  ``cancel_with_claim`` accepts that
    exact claim, stages and dispatches one managed CANCEL, and returns only a
    callback-backed observation.  A native queue receipt is never treated as
    provider acknowledgement.
    """

    def __init__(
        self,
        owner: CtpExecutionOwner,
        admission_gate: RiskExecutionCancelAdmissionGate,
        *,
        callback_pump_events: int = 32,
        query_timeout: float = 5.0,
        max_pending: int = 64,
        after_cancel_observation: (
            Callable[[CancelIntent, CancelObservation | None, bool], None] | None
        ) = None,
    ) -> None:
        if type(owner) is not CtpExecutionOwner:
            raise ContractValidationError("exact borrowed CTP execution owner is required")
        if type(admission_gate) is not RiskExecutionCancelAdmissionGate:
            raise ContractValidationError("exact Risk cancellation admission gate is required")
        if (
            type(callback_pump_events) is not int
            or not 1 <= callback_pump_events <= 256
        ):
            raise ContractValidationError("callback pump bound must be from 1 to 256")
        if (
            type(query_timeout) not in (int, float)
            or isinstance(query_timeout, bool)
            or not math.isfinite(query_timeout)
            or query_timeout <= 0
            or query_timeout > 5.0
        ):
            raise ContractValidationError("CTP cancel target query timeout is invalid")
        if type(max_pending) is not int or not 1 <= max_pending <= 64:
            raise ContractValidationError("pending cancel bound must be from 1 to 64")
        if after_cancel_observation is not None and not callable(
            after_cancel_observation
        ):
            raise ContractValidationError("cancel observation callback must be callable")

        store = owner._store
        scope = owner._scope
        writer_lease = owner._writer_lease
        session_adapter = owner._session_adapter
        reconciliation_port = owner._reconciliation_port
        if (
            type(store) is not SqliteExecutionStore
            or type(scope) is not ExecutionScope
            or type(writer_lease) is not WriterLease
            or type(session_adapter) is not CtpCallbackSessionStoreAdapter
            or type(reconciliation_port) is not CtpOwnerCallbackReconciliationPort
            or session_adapter.store is not store
            or session_adapter.scope != scope
            or session_adapter.writer_lease is not writer_lease
            or reconciliation_port._store is not store
            or reconciliation_port._scope != scope
            or reconciliation_port._writer_lease is not writer_lease
            or reconciliation_port._session_adapter is not session_adapter
            or reconciliation_port._owner_handle is not owner._owner_handle
            or admission_gate._store is not store
            or admission_gate._execution_scope != scope
            or scope.provider != "ctp"
            or scope.environment != "simulation"
            or not scope.trading_day
        ):
            raise ContractValidationError(
                "Risk gate and CTP owner do not share the exact Store scope"
            )

        owner._assert_worker_thread()
        owner._assert_active_owner()
        self._store = store
        self._scope = scope
        self._writer_lease = writer_lease
        self._owner = owner
        self._admission_gate = admission_gate
        self._reconciliation_port = reconciliation_port
        self._session_binding = owner._session_binding
        self._thread = current_thread()
        self._callback_pump_events = callback_pump_events
        self._query_timeout = float(query_timeout)
        self._max_pending = max_pending
        self._after_cancel_observation = after_cancel_observation
        self._query_evaluator = CtpOrderTargetQueryEvaluator(owner)
        self._authority_issuer = CtpCancelAuthorityIssuerV1(
            owner, admission_gate, max_pending=max_pending
        )
        self._busy = False
        self._targets: OrderedDict[str, _PreparedCancelTarget] = OrderedDict()
        self._queue: queue.Queue[_QueuedCancel] = queue.Queue(maxsize=1)

    def prepare_target(
        self, intent: CancelIntent
    ) -> CtpPreparedCancelAuthorizationFactsV1:
        """Query and retain the exact open target before Risk claims dispatch."""

        self._enter()
        try:
            durable_intent, cancel_record = self._validate_cancel_for_target(intent)
            existing_target = self._targets.get(intent.cancel_id)
            if existing_target is not None:
                if existing_target.intent != durable_intent:
                    raise ContractValidationError(
                        "cancel id is already bound to another durable intent"
                    )
                if existing_target.claim_consumed or existing_target.dispatch_started:
                    raise InvalidStateTransition(
                        "a claimed CTP cancellation target cannot be queried again"
                    )
                if self._target_is_fresh(existing_target.target_projection):
                    self._targets.move_to_end(intent.cancel_id)
                    return existing_target.authorization_facts
                del self._targets[intent.cancel_id]

            self._evict_expired_unclaimed_targets()
            if len(self._targets) >= self._max_pending:
                raise InvalidStateTransition("bounded CTP cancel target cache is full")

            reservation, submit_binding, submit_command, target_intent = (
                self._read_original_submit(durable_intent)
            )
            cancel_command_id = stable_managed_command_id(
                "cancel",
                durable_intent.target_intent_id,
                reservation.runtime_order_id,
                durable_intent.cancel_id,
            )
            existing_cancel_command = self._store.read_ctp_dispatch_command(
                self._scope, cancel_command_id
            )
            if existing_cancel_command is not None:
                raise InvalidStateTransition(
                    "existing CTP cancel command is readback-only"
                )

            if cancel_record.permit_reference is None:
                raise ContractValidationError("durable Risk cancellation permit is missing")
            self._admission_gate.validate(cancel_record.permit_reference, durable_intent)

            native_payload = submit_command.native_request_payload
            if not isinstance(native_payload, dict) and not hasattr(native_payload, "get"):
                raise ContractValidationError("durable CTP SUBMIT native payload is missing")
            instrument_id = native_payload.get("InstrumentID")
            if (
                type(instrument_id) is not str
                or not instrument_id
                or instrument_id != target_intent.instrument
            ):
                raise ContractValidationError(
                    "durable CTP SUBMIT instrument differs from its execution intent"
                )
            exchange_id, order_sys_id = decode_ctp_provider_order_id(
                durable_intent.provider_order_id
            )
            target_projection = self._query_evaluator.query_and_issue(
                durable_intent.target_intent_id,
                instrument_id=instrument_id,
                exchange_id=exchange_id,
                order_sys_id=order_sys_id,
                timeout=self._query_timeout,
            )
            self._validate_target_projection(
                durable_intent,
                target_intent,
                reservation,
                exchange_id,
                order_sys_id,
                target_projection,
            )
            facts = CtpPreparedCancelAuthorizationFactsV1(
                cancel_id=durable_intent.cancel_id,
                target_intent_id=durable_intent.target_intent_id,
                provider_order_id=durable_intent.provider_order_id,
                account_key=reservation.account_key,
                scope_key=reservation.scope_key,
                trading_day=reservation.trading_day,
                runtime_order_id=reservation.runtime_order_id,
                order_ref=reservation.order_ref,
                session_generation_id=target_projection.projection.session_generation_id,
                instrument_id=target_projection.projection.instrument_id,
                exchange_id=target_projection.projection.exchange_id,
            )
            self._targets[intent.cancel_id] = _PreparedCancelTarget(
                intent=durable_intent,
                target_projection=target_projection,
                submit_binding=submit_binding,
                authorization_facts=facts,
            )
            return facts
        finally:
            self._leave()

    def refresh_target(
        self, intent: CancelIntent
    ) -> CtpPreparedCancelAuthorizationFactsV1:
        """Discard one unclaimed cache entry and re-query the exact target."""

        self._enter()
        try:
            durable_intent = self._validate_durable_cancel_identity(intent)
            entry = self._targets.get(durable_intent.cancel_id)
            if entry is not None:
                if entry.intent != durable_intent:
                    raise ContractValidationError(
                        "cancel id is already bound to another durable intent"
                    )
                if entry.claim_consumed or entry.dispatch_started:
                    raise InvalidStateTransition(
                        "a claimed CTP cancellation target cannot be refreshed"
                    )
                del self._targets[durable_intent.cancel_id]
        finally:
            self._leave()
        return self.prepare_target(durable_intent)

    def cancel_with_claim(
        self,
        intent: CancelIntent,
        claim_receipt: CancelDispatchClaimReceiptV1,
    ) -> CancelObservation:
        """Dispatch once under the exact Risk claim and read callback evidence."""

        self._enter()
        entry: _PreparedCancelTarget | None = None
        durable_intent: CancelIntent | None = None
        safe_no_dispatch = False
        try:
            durable_intent, cancel_record = self._validate_cancel_for_dispatch(
                intent, claim_receipt
            )
            entry = self._targets.get(durable_intent.cancel_id)
            if entry is None or entry.intent != durable_intent:
                raise CtpManagedCancelNotReady("cancel_target_not_prepared")
            if entry.claim_consumed or entry.dispatch_started:
                raise CtpManagedCancelNotReady("cancel_dispatch_already_attempted")

            # From this point the facade has consumed Risk's one-use claim.
            # Any failure must remain UNKNOWN; this entry cannot be retried.
            entry.claim_consumed = True
            if not self._target_is_fresh(entry.target_projection):
                raise CtpManagedCancelNotReady("cancel_target_projection_expired")
            if cancel_record.permit_reference is None:
                raise CtpManagedCancelNotReady("cancel_risk_permit_missing")
            self._admission_gate.verify_dispatch_claim_receipt(
                durable_intent, claim_receipt
            )

            reservation = self._store.read_ctp_order_identity(
                self._scope, durable_intent.target_intent_id
            )
            if type(reservation) is not CtpOrderIdentityReservation:
                raise CtpManagedCancelNotReady("cancel_target_reservation_missing")
            command_id = stable_managed_command_id(
                "cancel",
                durable_intent.target_intent_id,
                reservation.runtime_order_id,
                durable_intent.cancel_id,
            )
            existing = self._store.read_ctp_dispatch_command(self._scope, command_id)
            if existing is not None:
                # The same durable command is always read-only on replay.
                cancel_binding = CtpManagedDispatchBinding.from_command(
                    existing, reservation
                )
                observed = self._read_cancel_observation(
                    durable_intent, cancel_binding, entry.submit_binding
                )
                if observed is None:
                    raise CtpManagedCancelNotReady("existing_cancel_not_terminal")
                del self._targets[durable_intent.cancel_id]
                self._notify_after_cancel_observation(
                    durable_intent, observed, entry.dispatch_started
                )
                return observed

            # The durable read proved no earlier CANCEL command exists. Errors
            # during the remaining local preparation are therefore known to be
            # pre-dispatch until the flag is cleared immediately before the
            # native owner call below.
            safe_no_dispatch = True

            native_request_id = self._owner._trader._next_request_id()
            if (
                type(native_request_id) is not int
                or not 1 <= native_request_id <= 2_147_483_647
            ):
                raise ContractValidationError("TraderClient returned an invalid RequestID")
            queue_receipt_id = uuid.uuid4().hex
            prepared = self._authority_issuer.prepare_cancel(
                durable_intent,
                claim_receipt,
                entry.target_projection,
                native_request_id=native_request_id,
                local_queue_receipt_id=queue_receipt_id,
            )
            if (
                type(prepared) is not CtpManagedPreparedDispatch
                or prepared.operation != "cancel"
                or prepared.managed_intent_id != durable_intent.target_intent_id
                or prepared.managed_cancel_intent_id != durable_intent.cancel_id
                or prepared.local_queue_receipt_id != queue_receipt_id
            ):
                raise InvalidStateTransition("cancel issuer returned a mismatched dispatch")
            envelope = _QueuedCancel(
                prepared=prepared, target_projection=entry.target_projection
            )
            try:
                self._queue.put_nowait(envelope)
            except queue.Full as error:
                raise InvalidStateTransition("bounded CTP cancel queue is full") from error
            try:
                queued = self._queue.get_nowait()
            except queue.Empty as error:
                self._queue.task_done()
                raise InvalidStateTransition(
                    "CTP cancel queue lost its enqueued request"
                ) from error
            if queued is not envelope:
                self._queue.task_done()
                raise InvalidStateTransition("CTP queue returned another cancel request")
            queue_receipt = {
                "kind": "command_receipt",
                "command": "cancel",
                "receipt_id": queue_receipt_id,
                "queued": True,
            }
            entry.dispatch_started = True
            safe_no_dispatch = False
            try:
                dispatch = self._owner.dispatch_cancel(
                    prepared,
                    queue_receipt,
                    cancel_target_projection=entry.target_projection,
                    authority_verifier=self._authority_issuer,
                )
            finally:
                self._queue.task_done()
            if (
                type(dispatch) is not CtpExecutionDispatchResult
                or type(dispatch.binding) is not CtpManagedDispatchBinding
                or dispatch.binding.operation != "cancel"
                or dispatch.binding.command_id != prepared.command_id
                or dispatch.binding.managed_intent_id != durable_intent.target_intent_id
                or dispatch.binding.managed_cancel_intent_id != durable_intent.cancel_id
            ):
                raise InvalidStateTransition("CTP owner returned a mismatched cancel binding")

            self._owner.pump_callbacks(self._callback_pump_events)
            observed = self._read_cancel_observation(
                durable_intent, dispatch.binding, entry.submit_binding
            )
            if observed is None:
                raise CtpManagedCancelNotReady("cancel_callback_not_ready")
            del self._targets[durable_intent.cancel_id]
            self._notify_after_cancel_observation(
                durable_intent, observed, entry.dispatch_started
            )
            return observed
        except Exception:
            if (
                durable_intent is not None
                and entry is not None
                and entry.claim_consumed
                and safe_no_dispatch
            ):
                try:
                    self._notify_after_cancel_observation(
                        durable_intent, None, False
                    )
                except Exception:
                    # Preserve the original cancel failure. The runtime callback
                    # retains its pending state and withdraws readiness on error.
                    pass
            raise
        finally:
            self._leave()

    def _notify_after_cancel_observation(
        self,
        intent: CancelIntent,
        observation: CancelObservation | None,
        dispatch_started: bool,
    ) -> None:
        callback = self._after_cancel_observation
        if callback is not None:
            callback(intent, observation, dispatch_started)

    def cancel(self, intent: CancelIntent) -> CancelObservation:
        """Fail closed: this port requires the facade's exact Risk claim."""

        raise ContractValidationError(
            "CTP managed cancellation requires cancel_with_claim and a Risk receipt"
        )

    def reconcile(self, intent: CancelIntent) -> CancelObservation | None:
        """Read an existing cancel action; never query a target or redispatch."""

        self._enter()
        try:
            durable_intent = self._validate_durable_cancel_identity(intent)
            reservation = self._store.read_ctp_order_identity(
                self._scope, durable_intent.target_intent_id
            )
            if type(reservation) is not CtpOrderIdentityReservation:
                return None
            cancel_command_id = stable_managed_command_id(
                "cancel",
                durable_intent.target_intent_id,
                reservation.runtime_order_id,
                durable_intent.cancel_id,
            )
            command = self._store.read_ctp_dispatch_command(
                self._scope, cancel_command_id
            )
            if type(command) is not CtpDispatchCommand:
                return None
            cancel_binding = CtpManagedDispatchBinding.from_command(command, reservation)
            submit_binding = self._read_existing_submit_binding(
                durable_intent.target_intent_id, reservation
            )
            if submit_binding is None:
                return None
            try:
                self._owner.pump_callbacks(self._callback_pump_events)
            except (ContractValidationError, InvalidStateTransition):
                # The reconciliation port below will report the owner/session
                # uncertainty without synthesizing callback state.
                pass
            observation = self._read_cancel_observation(
                durable_intent, cancel_binding, submit_binding
            )
            if (
                type(observation) is CancelObservation
                and observation.cancel_id == durable_intent.cancel_id
                and observation.target_intent_id == durable_intent.target_intent_id
                and observation.provider_order_id == durable_intent.provider_order_id
                and observation.state
                in {ExecutionState.CANCELLED, ExecutionState.REJECTED}
            ):
                self._notify_after_cancel_observation(
                    durable_intent, observation, True
                )
                prepared = self._targets.get(durable_intent.cancel_id)
                if prepared is not None and prepared.intent == durable_intent:
                    del self._targets[durable_intent.cancel_id]
            return observation
        finally:
            self._leave()

    def reconcile_cancel(self, intent: CancelIntent) -> CancelObservation | None:
        """Expose callback-only reconciliation to the cancellation facade.

        This explicit name avoids colliding with a submit-capable dispatcher's
        other reconciliation APIs. The delegated method keeps its owner-thread
        check and reads only the existing CTP callback/store state.
        """

        return self.reconcile(intent)

    def _read_cancel_observation(
        self,
        intent: CancelIntent,
        cancel_binding: CtpManagedDispatchBinding,
        submit_binding: CtpManagedDispatchBinding,
    ) -> CancelObservation | None:
        result = self._reconciliation_port.read_cancel_observation(
            cancel_binding, submit_binding
        )
        if type(result) is CtpOwnerCallbackObservationNotReadyV1:
            return None
        if type(result) is not CancelObservation or (
            result.cancel_id != intent.cancel_id
            or result.target_intent_id != intent.target_intent_id
            or result.provider_order_id != intent.provider_order_id
        ):
            raise InvalidStateTransition("CTP cancel observation differs from durable intent")
        return result

    def _validate_cancel_for_target(
        self, intent: CancelIntent
    ) -> tuple[CancelIntent, CancelRecord]:
        durable_intent = self._validate_durable_cancel_identity(intent)
        record = self._store.get_cancel(durable_intent.cancel_id, scope=self._scope)
        if type(record) is not CancelRecord or (
            record.cancel_id != durable_intent.cancel_id
            or record.scope_key != self._scope.key
            or record.target_intent_id != durable_intent.target_intent_id
            or record.provider_order_id != durable_intent.provider_order_id
            or record.payload_sha256 != durable_intent.fingerprint
            or record.state is not ExecutionState.DISPATCHING
            or record.dispatch_attempts != 1
            or record.review_required is not False
        ):
            raise ContractValidationError("durable cancellation is not the exact claimed attempt")
        return durable_intent, record

    def _validate_cancel_for_dispatch(
        self,
        intent: CancelIntent,
        claim_receipt: CancelDispatchClaimReceiptV1,
    ) -> tuple[CancelIntent, CancelRecord]:
        durable_intent, record = self._validate_cancel_for_target(intent)
        if type(claim_receipt) is not CancelDispatchClaimReceiptV1:
            raise ContractValidationError("exact Risk cancellation claim receipt is required")
        if record.permit_reference is None or (
            claim_receipt.permit_reference != record.permit_reference
            or claim_receipt.scope_key != self._scope.key
            or claim_receipt.cancel_id != durable_intent.cancel_id
            or claim_receipt.intent_fingerprint != durable_intent.fingerprint
        ):
            raise ContractValidationError("Risk cancellation receipt differs from durable claim")
        return durable_intent, record

    def _validate_durable_cancel_identity(self, intent: CancelIntent) -> CancelIntent:
        if type(intent) is not CancelIntent or intent.scope != self._scope:
            raise ContractValidationError("exact same-scope CTP cancel intent is required")
        self._store.assert_writer_lease(self._scope, self._writer_lease)
        stored = self._store.get_cancel_intent(intent.cancel_id, scope=self._scope)
        if type(stored) is not CancelIntent or stored != intent:
            raise InvalidStateTransition("CTP cancellation differs from its durable Store intent")
        return stored

    def _read_original_submit(
        self, cancel_intent: CancelIntent
    ) -> tuple[
        CtpOrderIdentityReservation,
        CtpManagedDispatchBinding,
        CtpDispatchCommand,
        OrderIntent,
    ]:
        target_intent = self._store.get_intent(
            cancel_intent.target_intent_id, scope=self._scope
        )
        reservation = self._store.read_ctp_order_identity(
            self._scope, cancel_intent.target_intent_id
        )
        if (
            type(target_intent) is not OrderIntent
            or target_intent.scope != self._scope
            or type(reservation) is not CtpOrderIdentityReservation
            or reservation.account_key != self._scope.account_key
            or reservation.scope_key != self._scope.key
            or reservation.trading_day != self._scope.trading_day
            or reservation.managed_intent_id != target_intent.intent_id
        ):
            raise ContractValidationError("exact original CTP target reservation is required")
        command_id = stable_managed_command_id(
            "submit", target_intent.intent_id, reservation.runtime_order_id
        )
        command = self._store.read_ctp_dispatch_command(self._scope, command_id)
        if type(command) is not CtpDispatchCommand or (
            command.operation != "SUBMIT"
            or command.command_id != command_id
            or command.account_key != self._scope.account_key
            or command.scope_key != self._scope.key
            or command.trading_day != self._scope.trading_day
            or command.reservation_managed_intent_id != target_intent.intent_id
            or command.status != "COMPLETED"
            or command.local_queue_receipt_queued is not True
        ):
            raise ContractValidationError("original CTP SUBMIT is not durably queued")
        binding = CtpManagedDispatchBinding.from_command(command, reservation)
        if (
            binding.operation != "submit"
            or binding.command_id != command_id
            or binding.managed_intent_id != target_intent.intent_id
            or binding.runtime_order_id != reservation.runtime_order_id
            or binding.order_ref != reservation.order_ref
            or binding.local_queue_receipt_queued is not True
        ):
            raise ContractValidationError("original CTP SUBMIT binding is inconsistent")
        return reservation, binding, command, target_intent

    def _read_existing_submit_binding(
        self, target_intent_id: str, reservation: CtpOrderIdentityReservation
    ) -> CtpManagedDispatchBinding | None:
        command_id = stable_managed_command_id(
            "submit", target_intent_id, reservation.runtime_order_id
        )
        command = self._store.read_ctp_dispatch_command(self._scope, command_id)
        if type(command) is not CtpDispatchCommand or (
            command.operation != "SUBMIT"
            or command.status != "COMPLETED"
            or command.local_queue_receipt_queued is not True
            or command.reservation_managed_intent_id != target_intent_id
        ):
            return None
        binding = CtpManagedDispatchBinding.from_command(command, reservation)
        if (
            binding.operation != "submit"
            or binding.command_id != command_id
            or binding.managed_intent_id != target_intent_id
            or binding.runtime_order_id != reservation.runtime_order_id
            or binding.order_ref != reservation.order_ref
            or binding.local_queue_receipt_queued is not True
        ):
            return None
        return binding

    def _validate_target_projection(
        self,
        cancel_intent: CancelIntent,
        target_intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        exchange_id: str,
        order_sys_id: str,
        handle: Any,
    ) -> None:
        if type(handle) is not CtpOrderTargetProjectionHandle:
            raise InvalidStateTransition("CTP target query did not issue a typed Store handle")
        projection = handle.projection
        owner_binding = self._owner._assert_active_owner()
        if (
            projection.account_key != self._scope.account_key
            or projection.scope_key != self._scope.key
            or projection.trading_day != self._scope.trading_day
            or projection.managed_intent_id != target_intent.intent_id
            or projection.runtime_order_id != reservation.runtime_order_id
            or projection.order_ref != reservation.order_ref
            or projection.instrument_id != target_intent.instrument
            or projection.exchange_id != exchange_id
            or projection.order_sys_id != order_sys_id
            or projection.provider_state not in {"OPEN", "PARTIAL"}
            or projection.session_generation_id != owner_binding.session_generation_id
            or encode_ctp_provider_order_id(
                projection.exchange_id, projection.order_sys_id
            )
            != cancel_intent.provider_order_id
            or type(projection.expires_at_ns) is not int
            or projection.expires_at_ns <= time.monotonic_ns()
        ):
            raise ContractValidationError("fresh CTP target projection differs from cancel intent")

    @staticmethod
    def _target_is_fresh(handle: CtpOrderTargetProjectionHandle) -> bool:
        return (
            type(handle) is CtpOrderTargetProjectionHandle
            and type(handle.projection.expires_at_ns) is int
            and time.monotonic_ns() < handle.projection.expires_at_ns
        )

    def _evict_expired_unclaimed_targets(self) -> None:
        for cancel_id, entry in tuple(self._targets.items()):
            if not entry.claim_consumed and not entry.dispatch_started:
                if not self._target_is_fresh(entry.target_projection):
                    del self._targets[cancel_id]

    def _enter(self) -> None:
        if current_thread() is not self._thread:
            raise ContractValidationError("CTP managed cancel port is thread-confined")
        if self._busy:
            raise InvalidStateTransition("CTP managed cancel port does not allow reentry")
        self._owner._assert_worker_thread()
        self._store.assert_writer_lease(self._scope, self._writer_lease)
        self._busy = True

    def _leave(self) -> None:
        self._busy = False


__all__ = [
    "CtpManagedCancelNotReady",
    "CtpManagedCancelPort",
    "CtpPreparedCancelAuthorizationFactsV1",
]
