"""Managed facade port for one borrowed, active CTP session.

This adapter composes an already-active TraderClient, its exact callback
session, the durable CTP outbox, BasicAdmission, and callback reconciliation.
It does not own or change native-session lifecycle. Queue/native return values
are local dispatch facts only; ProviderObservation comes only from the durable
callback snapshot.
"""

from __future__ import annotations

import hashlib
import queue
import uuid
from dataclasses import dataclass
from threading import current_thread
from typing import Any

from bt_api_execution import (
    ContractValidationError,
    CtpCallbackSessionStoreAdapter,
    CtpOrderIdentityReservation,
    CtpOwnerCallbackObservationNotReadyV1,
    ExecutionAdmissionAuthorizationV1,
    ExecutionBasicAdmissionGate,
    ExecutionProviderOrderParametersV1,
    ExecutionProviderValidatorV1,
    ExecutionRecord,
    ExecutionScope,
    ExecutionState,
    InvalidStateTransition,
    OrderIntent,
    ProviderObservation,
    SqliteExecutionStore,
    WriterLease,
    WriterLeaseUnavailable,
    canonical_json,
)
from bt_api_execution.ctp_single_worker_candidate import (
    CtpManagedDispatchBinding,
    stable_managed_command_id,
)
from bt_api_py.ctp_admission_authority import (
    CtpAdmissionAuthorityIssuerV1,
    CtpNativeDispatchBindingV1,
)
from bt_api_py.ctp_execution_owner import CtpExecutionDispatchResult, CtpExecutionOwner
from bt_api_py.ctp_service_execution import CtpOwnerCallbackReconciliationPort


@dataclass(frozen=True, slots=True)
class _QueuedSubmit:
    prepared: Any
    receipt_id: str


class CtpManagedSubmitNotReady(InvalidStateTransition):
    """The durable callback snapshot is not ready to reconcile this submit."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"CTP provider observation is not ready: {reason_code}")


class CtpManagedSubmitPort:
    """Submit-only facade port bound to one existing CTP session and Store.

    Construct this exact object before registering the BasicAdmission
    validator. Call bind_basic_admission once with the Gate built around that
    validator. Calls are confined to the creating thread. The borrowed
    TraderClient and session remain owned by their existing account owner.
    """

    def __init__(
        self,
        store: SqliteExecutionStore,
        scope: ExecutionScope,
        writer_lease: WriterLease,
        trader: Any,
        session_adapter: CtpCallbackSessionStoreAdapter,
        reconciliation_port: CtpOwnerCallbackReconciliationPort,
        native_binding: CtpNativeDispatchBindingV1,
        *,
        session_generation_id: str,
        callback_pump_events: int = 32,
    ) -> None:
        if type(store) is not SqliteExecutionStore:
            raise ContractValidationError("exact execution Store is required")
        if (
            type(scope) is not ExecutionScope
            or scope.provider != "ctp"
            or scope.environment != "simulation"
            or not scope.trading_day
        ):
            raise ContractValidationError("canonical CTP simulation scope is required")
        if type(writer_lease) is not WriterLease:
            raise ContractValidationError("typed CTP writer lease is required")
        if type(session_adapter) is not CtpCallbackSessionStoreAdapter or (
            session_adapter.store is not store
            or session_adapter.scope != scope
            or session_adapter.writer_lease is not writer_lease
        ):
            raise ContractValidationError("exact callback session adapter is required")
        if type(reconciliation_port) is not CtpOwnerCallbackReconciliationPort or (
            reconciliation_port._store is not store
            or reconciliation_port._scope != scope
            or reconciliation_port._writer_lease is not writer_lease
            or reconciliation_port._session_adapter is not session_adapter
        ):
            raise ContractValidationError("exact callback reconciliation port is required")
        if type(native_binding) is not CtpNativeDispatchBindingV1 or (
            native_binding.account_ref != scope.account_ref
            or native_binding.scope_key != scope.key
        ):
            raise ContractValidationError("exact CTP native account mapping is required")
        if (
            type(session_generation_id) is not str
            or not session_generation_id
            or session_generation_id != session_generation_id.strip()
        ):
            raise ContractValidationError("exact active CTP session generation is required")
        if (
            type(callback_pump_events) is not int
            or not 1 <= callback_pump_events <= 256
        ):
            raise ContractValidationError("callback pump bound must be from 1 to 256")

        self._store = store
        self._scope = scope
        self._writer_lease = writer_lease
        self._trader = trader
        self._session_adapter = session_adapter
        self._reconciliation_port = reconciliation_port
        self._native_binding = native_binding
        self._session_generation_id = session_generation_id
        self._callback_pump_events = callback_pump_events
        self._thread = current_thread()
        self._binding_attempted = False
        self._gate: ExecutionBasicAdmissionGate | None = None
        self._issuer: CtpAdmissionAuthorityIssuerV1 | None = None
        self._owner: CtpExecutionOwner | None = None
        self._busy = False
        # The owner receives a queue receipt only after put_nowait succeeds and
        # this synchronous thread retrieves the exact enqueued envelope.
        self._queue: queue.Queue[_QueuedSubmit] = queue.Queue(maxsize=1)

    def bind_basic_admission(self, gate: ExecutionBasicAdmissionGate) -> None:
        """Bind the exact retained-authorization Gate exactly once."""

        self._assert_thread()
        if self._binding_attempted:
            raise InvalidStateTransition("CTP submit port admission binding is one-shot")
        self._binding_attempted = True
        if type(gate) is not ExecutionBasicAdmissionGate or (
            gate._store is not self._store
            or gate._retain_claimed_authorizations is not True
        ):
            raise ContractValidationError(
                "same-Store BasicAdmission Gate with retained claims is required"
            )
        validator = gate._validators._validators.get(self._scope.provider)
        if (
            type(validator) is not ExecutionProviderValidatorV1
            or validator.dispatch_port is not self
        ):
            raise ContractValidationError(
                "BasicAdmission validator must be registered to this exact CTP port"
            )

        issuer = CtpAdmissionAuthorityIssuerV1(
            self._store,
            self._scope,
            self._writer_lease,
            gate,
            self._session_adapter,
            self,
            self._native_binding,
        )
        owner = CtpExecutionOwner(
            self._store,
            self._scope,
            self._writer_lease,
            issuer,
            self._trader,
            self._session_adapter,
            self._reconciliation_port,
            session_generation_id=self._session_generation_id,
        )
        self._gate = gate
        self._issuer = issuer
        self._owner = owner

    def submit_authorized(
        self,
        intent: OrderIntent,
        authorization: ExecutionAdmissionAuthorizationV1,
    ) -> ProviderObservation:
        """Dispatch one exact BasicAdmission claim and read callback evidence.

        Any missing or incomplete callback projection raises a typed
        not-ready error. The facade can then preserve the attempt as UNKNOWN;
        this port never retries or fabricates an acknowledgement.
        """

        self._enter()
        try:
            gate, issuer, owner = self._require_bound()
            self._validate_authorized_dispatch(gate, owner, intent, authorization)

            reservation = self._get_or_reserve_order_identity(intent)
            command_id = stable_managed_command_id(
                "submit", intent.intent_id, reservation.runtime_order_id
            )
            existing = self._store.read_ctp_dispatch_command(self._scope, command_id)
            if existing is not None:
                # A prior staged, claimed, UNKNOWN, or terminal command is
                # never sent a second time, even if its callback is not ready.
                observed = self._read_observation(
                    owner,
                    intent,
                    CtpManagedDispatchBinding.from_command(existing, reservation),
                    not_ready_is_none=False,
                )
                assert observed is not None
                return observed

            request_id = self._trader._next_request_id()
            if type(request_id) is not int or not 1 <= request_id <= 2_147_483_647:
                raise ContractValidationError("TraderClient returned an invalid RequestID")
            receipt_id = uuid.uuid4().hex
            prepared = issuer.prepare_submit(
                intent,
                authorization,
                native_request_id=request_id,
                local_queue_receipt_id=receipt_id,
            )
            envelope = _QueuedSubmit(prepared=prepared, receipt_id=receipt_id)
            try:
                self._queue.put_nowait(envelope)
            except queue.Full as error:
                raise InvalidStateTransition("bounded CTP submit queue is full") from error
            try:
                queued = self._queue.get_nowait()
            except queue.Empty as error:
                self._queue.task_done()
                raise InvalidStateTransition(
                    "CTP submit queue lost its enqueued request"
                ) from error
            if queued is not envelope:
                self._queue.task_done()
                raise InvalidStateTransition("CTP queue did not return its enqueued submit")
            receipt = {
                "kind": "command_receipt",
                "command": "submit",
                "receipt_id": queued.receipt_id,
                "queued": True,
            }
            try:
                dispatch = owner.dispatch_submit(queued.prepared, receipt)
                if type(dispatch) is not CtpExecutionDispatchResult:
                    raise InvalidStateTransition("CTP owner returned an invalid dispatch result")
                return self._read_observation(
                    owner, intent, dispatch.binding, not_ready_is_none=False
                )
            finally:
                self._queue.task_done()
        finally:
            self._leave()

    def query(self, intent: OrderIntent) -> ProviderObservation | None:
        """Read callback-derived provider state for this exact durable intent.

        Query may apply a bounded batch of already-persisted callback ingress.
        It allocates no identifiers, stages no command, and never submits.
        Not-ready callback state is represented as None for the facade.
        """

        self._enter()
        try:
            _gate, _issuer, owner = self._require_bound()
            self._validate_durable_intent(intent)
            reservation = self._store.read_ctp_order_identity(self._scope, intent.intent_id)
            if reservation is None:
                return None
            self._validate_reservation(intent, reservation)
            command_id = stable_managed_command_id(
                "submit", intent.intent_id, reservation.runtime_order_id
            )
            command = self._store.read_ctp_dispatch_command(self._scope, command_id)
            if command is None:
                return None
            binding = CtpManagedDispatchBinding.from_command(command, reservation)
            return self._read_observation(
                owner, intent, binding, not_ready_is_none=True
            )
        finally:
            self._leave()

    def _assert_thread(self) -> None:
        if current_thread() is not self._thread:
            raise ContractValidationError("CTP submit port is confined to its creating thread")

    def _enter(self) -> None:
        self._assert_thread()
        if self._busy:
            raise InvalidStateTransition("CTP submit port does not allow reentrant calls")
        if self._owner is None or self._issuer is None or self._gate is None:
            raise InvalidStateTransition("CTP submit port is not bound to BasicAdmission")
        self._busy = True

    def _leave(self) -> None:
        self._busy = False

    def _require_bound(
        self,
    ) -> tuple[
        ExecutionBasicAdmissionGate,
        CtpAdmissionAuthorityIssuerV1,
        CtpExecutionOwner,
    ]:
        if self._gate is None or self._issuer is None or self._owner is None:
            raise InvalidStateTransition("CTP submit port is not bound to BasicAdmission")
        return self._gate, self._issuer, self._owner

    def _validate_authorized_dispatch(
        self,
        gate: ExecutionBasicAdmissionGate,
        owner: CtpExecutionOwner,
        intent: OrderIntent,
        authorization: ExecutionAdmissionAuthorizationV1,
    ) -> None:
        if type(intent) is not OrderIntent or intent.scope != self._scope:
            raise ContractValidationError("exact same-scope CTP submit intent is required")
        if type(authorization) is not ExecutionAdmissionAuthorizationV1:
            raise ContractValidationError("exact BasicAdmission authorization is required")

        # Authenticate before the first OrderRef, RequestID, or queue mutation.
        gate.verify_claimed_authorization(
            authorization, intent, self, self._writer_lease
        )
        self._store.assert_writer_lease(self._scope, self._writer_lease)
        owner._assert_worker_thread()
        session = owner._assert_active_owner()
        if session.session_generation_id != self._session_generation_id:
            raise InvalidStateTransition("active CTP generation changed")
        parameters = authorization.parameters
        if (
            type(parameters) is not ExecutionProviderOrderParametersV1
            or intent.instrument != self._native_binding.intent_instrument
            or self._native_binding.intent_instrument
            != self._native_binding.provider_instrument
            or parameters.provider_instrument
            != self._native_binding.provider_instrument
        ):
            raise ContractValidationError(
                "BasicAdmission instrument differs from the fixed CTP native mapping"
            )
        login_facts = self._store.read_ctp_callback_session_context_facts(
            owner._owner_handle
        )
        if (
            type(login_facts) is not tuple
            or len(login_facts) != 3
            or login_facts[0] != session
            or type(login_facts[2]) is not str
            or login_facts[2] != self._native_binding.investor_id
        ):
            raise ContractValidationError(
                "explicit CTP InvestorID differs from the active login mapping"
            )
        self._validate_durable_intent(intent)
        record = self._store.get(intent.intent_id, scope=self._scope)
        if type(record) is not ExecutionRecord or (
            record.intent_id != intent.intent_id
            or record.scope_key != self._scope.key
            or record.payload_sha256 != intent.fingerprint
            or record.state is not ExecutionState.DISPATCHING
            or record.dispatch_attempts != 1
            or record.review_required is not False
            or record.basic_admission_reference != authorization.permit_reference
            or record.admission_context_sha256 != authorization.context_fingerprint
        ):
            raise ContractValidationError(
                "durable CTP intent is not the exact BasicAdmission dispatch claim"
            )

    def _validate_durable_intent(self, intent: OrderIntent) -> None:
        if type(intent) is not OrderIntent or intent.scope != self._scope:
            raise ContractValidationError("exact same-scope CTP order intent is required")
        stored = self._store.get_intent(intent.intent_id, scope=self._scope)
        if stored is None or stored != intent:
            raise InvalidStateTransition("CTP order intent differs from the durable Store")

    @staticmethod
    def _runtime_order_id(intent: OrderIntent) -> str:
        material = canonical_json(
            {
                "domain": "bt_api_py.ctp.managed-runtime-order-id.v1",
                "scope_key": intent.scope.key,
                "intent_id": intent.intent_id,
                "intent_fingerprint": intent.fingerprint,
            }
        )
        return "bt-managed-v1:" + hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _get_or_reserve_order_identity(
        self, intent: OrderIntent
    ) -> CtpOrderIdentityReservation:
        reservation = self._store.read_ctp_order_identity(self._scope, intent.intent_id)
        if reservation is None:
            reservation = self._store.reserve_ctp_order_identity(
                self._scope, intent.intent_id, self._runtime_order_id(intent)
            )
        # A cutover may already have made a valid durable reservation with a
        # different trusted allocator. Reuse that exact Store identity rather
        # than replacing or second-guessing its runtime ID.
        self._validate_reservation(intent, reservation)
        return reservation

    def _validate_reservation(
        self,
        intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
    ) -> None:
        if type(reservation) is not CtpOrderIdentityReservation or (
            reservation.account_key != self._scope.account_key
            or reservation.scope_key != self._scope.key
            or reservation.trading_day != self._scope.trading_day
            or reservation.managed_intent_id != intent.intent_id
        ):
            raise InvalidStateTransition("CTP OrderRef reservation differs from this intent")

    def _read_observation(
        self,
        owner: CtpExecutionOwner,
        intent: OrderIntent,
        binding: CtpManagedDispatchBinding,
        *,
        not_ready_is_none: bool,
    ) -> ProviderObservation | None:
        # A poisoned/stale owner cannot pump, but the reconciliation reader
        # still reports the durable UNKNOWN/fence reason from its Store snapshot.
        try:
            owner._assert_active_owner()
        except (ContractValidationError, InvalidStateTransition, WriterLeaseUnavailable):
            pass
        else:
            owner.pump_callbacks(self._callback_pump_events)
        result = owner.read_provider_observation(intent, binding)
        if type(result) is ProviderObservation:
            if result.intent_id != intent.intent_id:
                raise InvalidStateTransition("CTP provider observation differs from intent")
            return result
        if type(result) is CtpOwnerCallbackObservationNotReadyV1:
            if not_ready_is_none:
                return None
            raise CtpManagedSubmitNotReady(result.reason_code)
        raise InvalidStateTransition("CTP callback reader returned an unknown result")


__all__ = ["CtpManagedSubmitNotReady", "CtpManagedSubmitPort"]
