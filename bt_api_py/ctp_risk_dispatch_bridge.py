"""Bind authenticated Execution admission and zero-fill CTP evidence to Risk.

The bridge records one Risk reservation/claim before native dispatch. After
the Execution facade settles an exact ACK observation, it may resolve only a
reader-issued, read-back-verified zero-fill OPEN order into tracked exposure.
Positive fills and terminal outcomes remain outside this bridge slice.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from threading import current_thread
from typing import Any

from bt_api_execution import (
    ContractValidationError,
    ExecutionEvent,
    ExecutionRecord,
    ExecutionScope,
    ExecutionState,
    InvalidStateTransition,
    OrderIntent,
    PositionEffect,
    WriterLease,
    payload_sha256,
)
from bt_api_execution.basic_admission import ExecutionAdmissionContextV1
from bt_api_execution.ctp_single_worker_candidate import CtpManagedDispatchBinding
from bt_api_risk import (
    AccountScope,
    DispatchClaimBinding,
    DispatchEvidenceClass,
    DispatchResolutionReceipt,
    DispatchResolutionReceiptStatus,
    DispatchTrackedOrderProof,
    IntentAction,
    RiskIntent,
    RiskPermit,
)
from bt_api_risk.order_admission import (
    DurableRiskAdmissionAdapter,
    RiskAdmissionDeniedError,
)

from bt_api_py._contracts.models import OrderRequest
from bt_api_py._contracts.models import OrderType as BtOrderType
from bt_api_py._contracts.models import Side as BtSide
from bt_api_py.service_risk_order_journal import (
    RiskOrderJournal,
    RiskOrderJournalEvent,
    RiskOrderWriterFence,
    sha256_text,
)
from bt_api_py.service_risk_order_journal import (
    canonical_json as risk_canonical_json,
)


class CtpRiskDispatchError(InvalidStateTransition):
    """Finite fail-closed error from the CTP/Risk command handoff."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not code or not code.replace("_", "").isalnum():
            code = "CTP_RISK_DISPATCH_FAILED"
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class _PermitState:
    intent: OrderIntent
    risk_intent: RiskIntent
    permit: RiskPermit
    principal_ref: str
    risk_intent_id: str
    state: str
    claim: DispatchClaimBinding | None = None
    claim_event: RiskOrderJournalEvent | None = None
    claim_event_updated_at_ns: int | None = None
    dispatch_event: RiskOrderJournalEvent | None = None
    zero_fill_reader: Any | None = None
    zero_fill_evidence: Any | None = None
    tracked_proof: DispatchTrackedOrderProof | None = None
    tracked_receipt: DispatchResolutionReceipt | None = None
    tracked_event: RiskOrderJournalEvent | None = None
    tracked_source_event: ExecutionEvent | None = None
    tracked_update_review_source_event: ExecutionEvent | None = None


class CtpRiskDispatchBridge:
    """Join trusted Execution admission, one Risk claim, and one CTP command.

    ``adapter`` and ``journal`` are supplied by trusted service composition.
    The bridge is bound to the exact live CTP owner after its submit port has
    bound BasicAdmission. All methods that touch the journal or owner are
    confined to that same thread. It never mints a Risk scope, maps a caller
    claim, or treats a native queue result as provider evidence. Its only Risk
    resolution is a callback/query verified zero-fill OPEN order.
    """

    def __init__(
        self,
        adapter: DurableRiskAdmissionAdapter,
        journal: RiskOrderJournal,
        *,
        account_scope: AccountScope,
        exchange_name: str = "SHFE",
    ) -> None:
        if type(adapter) is not DurableRiskAdmissionAdapter:
            raise ContractValidationError("exact durable Risk admission adapter is required")
        if type(journal) is not RiskOrderJournal or type(account_scope) is not AccountScope:
            raise ContractValidationError("exact Risk journal and account scope are required")
        if (
            type(exchange_name) is not str
            or not exchange_name
            or exchange_name != exchange_name.strip()
            or not exchange_name.isascii()
            or len(exchange_name) > 24
        ):
            raise ContractValidationError("invalid configured CTP exchange name")

        self._adapter = adapter
        self._gate = adapter._gate
        self._journal = journal
        self._account_scope = account_scope
        self._exchange_name = exchange_name
        self._thread = current_thread()
        self._owner: Any | None = None
        self._closed = False
        self._close_uncertain = False
        self._permits: dict[str, _PermitState] = {}
        self._by_command: dict[str, _PermitState] = {}
        if getattr(self._gate, "_execution_journal_authority", None) is not journal:
            raise ContractValidationError(
                "Risk gate and CTP bridge must share the exact execution journal authority"
            )
        self._fence_cm = journal.writer_fence(account_scope)
        try:
            self._fence = self._fence_cm.__enter__()
        except Exception:
            self._fence_cm = None
            raise
        if type(self._fence) is not RiskOrderWriterFence or self._fence.scope != account_scope:
            try:
                self._fence_cm.__exit__(None, None, None)
            finally:
                self._fence_cm = None
            raise ContractValidationError("Risk writer fence does not match configured account")

    @property
    def account_scope(self) -> AccountScope:
        return self._account_scope

    @property
    def _journal_fence(self) -> RiskOrderWriterFence:
        return self._fence

    def is_bound_to(self, gate: object) -> bool:
        """Return true only for this adapter's exact durable Risk gate."""

        return self._adapter.is_bound_to(gate)

    def bind_execution_owner(self, owner: object) -> None:
        """Bind the exact CTP execution owner once, on its confined thread."""

        self._assert_open_thread()
        from bt_api_py.ctp_execution_owner import CtpExecutionOwner

        if type(owner) is not CtpExecutionOwner:
            raise ContractValidationError("exact CTP execution owner is required")
        if self._owner is not None:
            raise InvalidStateTransition("CTP Risk bridge owner binding is one-shot")
        if (
            owner._store is None
            or type(owner._scope) is not ExecutionScope
            or owner._scope.provider != self._account_scope.provider
            or owner._scope.environment != self._account_scope.environment
            or owner._scope.account_ref != self._adapter._expected_wire_account_ref(self._account_scope)
            or type(owner._writer_lease) is not WriterLease
            or owner._store.assert_writer_lease(owner._scope, owner._writer_lease) is not None
        ):
            raise ContractValidationError("CTP owner does not match the exact Risk account")
        self._owner = owner

    def reserve(self, intent: Any) -> RiskPermit:
        """Reject untrusted or unauthenticated reservation entry points."""

        raise CtpRiskDispatchError("AUTHENTICATED_CONTEXT_REQUIRED")

    def reserve_for_authenticated_execution(
        self,
        intent: OrderIntent,
        *,
        admission_context: ExecutionAdmissionContextV1,
        writer_lease: WriterLease,
        dispatch_port: object,
    ) -> RiskPermit:
        """Persist PREPARED, reserve Risk, then persist the exact reservation."""

        self._assert_open_thread()
        owner = self._require_owner()
        if (
            type(intent) is not OrderIntent
            or type(admission_context) is not ExecutionAdmissionContextV1
            or type(writer_lease) is not WriterLease
            or writer_lease is not owner._writer_lease
            or intent.scope != owner._scope
            or admission_context.execution_scope != intent.scope
            or admission_context.principal_ref != admission_context.identity.principal_ref
            or admission_context.action != "orders.submit"
        ):
            raise CtpRiskDispatchError("AUTHENTICATED_CONTEXT_MISMATCH")
        from bt_api_py.ctp_managed_submit_port import CtpManagedSubmitPort

        if (
            type(dispatch_port) is not CtpManagedSubmitPort
            or dispatch_port.execution_owner is not owner
        ):
            raise CtpRiskDispatchError("AUTHENTICATED_CONTEXT_MISMATCH")
        owner._store.assert_writer_lease(owner._scope, writer_lease)

        try:
            mapped = self._adapter._map_order(intent)
        except Exception:
            raise CtpRiskDispatchError("RISK_INTENT_MAPPING_FAILED") from None
        if (
            type(mapped) is not RiskIntent
            or mapped.scope != self._account_scope
            or mapped.intent_id != intent.intent_id
            or mapped.scope.provider != intent.scope.provider
            or mapped.scope.environment != intent.scope.environment
            or intent.scope.account_ref
            != self._adapter._expected_wire_account_ref(mapped.scope)
            or (mapped.strategy_id is not None and mapped.strategy_id != intent.scope.strategy_id)
        ):
            raise CtpRiskDispatchError("RISK_INTENT_SCOPE_MISMATCH")
        expected_action = {
            PositionEffect.OPEN: IntentAction.INCREASE,
            PositionEffect.CLOSE: IntentAction.REDUCE,
            PositionEffect.CLOSE_TODAY: IntentAction.REDUCE,
            PositionEffect.CLOSE_YESTERDAY: IntentAction.REDUCE,
        }.get(intent.position_effect)
        # Execution's quantity is the exact CTP Volume count. The CTP native
        # request has no product-size conversion, so the Risk mapping must use
        # the same canonical contract unit and economic dimensions.
        if (
            expected_action is None
            or type(mapped.action) is not IntentAction
            or mapped.action is not expected_action
            or mapped.instrument != intent.instrument
            or mapped.quantity != intent.quantity
            or mapped.quantity_unit not in {"contracts", "lots"}
            or mapped.position_side != intent.position_side
            or mapped.position_bucket != intent.position_bucket
        ):
            raise CtpRiskDispatchError("RISK_INTENT_ECONOMIC_MISMATCH")

        principal_ref = admission_context.principal_ref
        wire_scope = self._wire_scope_payload(admission_context)
        # The adapter's mapped RiskIntent is the authority for this identity.
        # Keep it distinct by domain from Execution's intent only where the
        # mapper actually does so; never mint a parallel synthetic Risk ID.
        risk_intent_id = mapped.intent_id
        canonical_payload = {
            "schema": "ctp-managed-risk-order-v1",
            "execution_intent": intent.to_payload(),
            "execution_intent_sha256": intent.fingerprint,
            "risk_intent_sha256": mapped.fingerprint,
        }
        request = self._order_request(intent, mapped)
        request_digest = sha256_text(
            risk_canonical_json(
                {
                    "exchange_name": self._exchange_name,
                    "operation": "BtApi.make_order(normalized=True)",
                    "request": request,
                }
            )
        )
        canonical_payload_digest = sha256_text(risk_canonical_json(canonical_payload))
        try:
            command, created = self._journal.prepare_or_get(
                self._fence,
                principal_ref=principal_ref,
                intent_id=intent.intent_id,
                risk_intent_id=risk_intent_id,
                payload_sha256=canonical_payload_digest,
                canonical_payload=canonical_payload,
                wire_scope=wire_scope,
                exchange_name=self._exchange_name,
                provider_account_id=self._account_scope.account_id,
                client_order_id=intent.intent_id,
                order_request=request,
                order_request_sha256=request_digest,
            )
        except Exception:
            raise CtpRiskDispatchError("RISK_PREPARE_FAILED") from None
        if not created or command.state != "PREPARED":
            raise CtpRiskDispatchError("RISK_COMMAND_REPLAY_UNSUPPORTED")

        try:
            permit = self._adapter.reserve(intent)
            if type(permit) is not RiskPermit or permit.intent_id != mapped.intent_id:
                raise CtpRiskDispatchError("RISK_PERMIT_INVALID")
            self._journal.append_event(
                self._fence,
                principal_ref=principal_ref,
                intent_id=intent.intent_id,
                event_type="RESERVED",
                payload={"permit_id": permit.permit_id, "risk_intent_id": risk_intent_id},
            )
        except Exception as error:
            # Risk reservation is still unclaimed here. Attempt only the
            # adapter's normal unclaimed release; never hide either failure.
            if "permit" in locals() and type(permit) is RiskPermit:
                try:
                    self._adapter.release(permit.permit_id, "ctp_journal_reservation_failed")
                except Exception:
                    raise CtpRiskDispatchError("RISK_RESERVATION_JOURNAL_FAILED") from None
            if isinstance(error, RiskAdmissionDeniedError):
                try:
                    self._journal.append_event(
                        self._fence,
                        principal_ref=principal_ref,
                        intent_id=intent.intent_id,
                        event_type="ADMISSION_DENIED",
                        payload={"code": error.code, "risk_intent_id": risk_intent_id},
                    )
                except Exception:
                    raise CtpRiskDispatchError("RISK_DENIAL_JOURNAL_FAILED") from None
                raise
            if isinstance(error, CtpRiskDispatchError):
                raise
            raise CtpRiskDispatchError("RISK_RESERVATION_FAILED") from None

        state = _PermitState(
            intent=intent,
            risk_intent=mapped,
            permit=permit,
            principal_ref=principal_ref,
            risk_intent_id=risk_intent_id,
            state="RESERVED",
        )
        self._permits[permit.permit_id] = state
        return permit

    def validate(self, permit_reference: str, intent: OrderIntent) -> RiskPermit:
        self._assert_open_thread()
        state = self._require_permit(permit_reference, intent)
        if state.state != "RESERVED":
            raise CtpRiskDispatchError("RISK_PERMIT_STATE_CONFLICT")
        try:
            permit = self._adapter.validate(permit_reference, intent)
        except Exception:
            raise CtpRiskDispatchError("RISK_PERMIT_REVALIDATION_FAILED") from None
        if type(permit) is not RiskPermit or permit.permit_id != permit_reference:
            raise CtpRiskDispatchError("RISK_PERMIT_INVALID")
        return permit

    def claim_for_dispatch(self, permit_reference: str, intent: OrderIntent) -> RiskPermit:
        """Use the existing Risk claim exactly once and journal its readback."""

        self._assert_open_thread()
        state = self._require_permit(permit_reference, intent)
        if state.state != "RESERVED":
            raise CtpRiskDispatchError("RISK_CLAIM_REPLAY_REJECTED")
        state.state = "CLAIMING"
        try:
            permit = self._adapter.claim_for_dispatch(permit_reference, intent)
            claim = self._gate.dispatch_claim_binding(permit_reference)
            if (
                type(permit) is not RiskPermit
                or permit.permit_id != permit_reference
                or type(claim) is not DispatchClaimBinding
                or claim.scope != self._account_scope
                or claim.permit_id != permit_reference
                or claim.intent_id != state.risk_intent.intent_id
                or claim.intent_hash != state.risk_intent.fingerprint
                or not _sha256(claim.claim_digest)
                or type(claim.claimed_at) not in (int, float)
                or type(claim.claimed_at) is bool
                or not math.isfinite(float(claim.claimed_at))
            ):
                raise CtpRiskDispatchError("RISK_CLAIM_BINDING_INVALID")
            claim_payload = {
                "claim": self._claim_payload(claim),
                "permit_id": permit_reference,
                "risk_intent_id": state.risk_intent_id,
            }
            claim_event = self._journal.append_event(
                self._fence,
                principal_ref=state.principal_ref,
                intent_id=intent.intent_id,
                event_type="CLAIMED",
                payload=claim_payload,
            )
            if (
                type(claim_event) is not RiskOrderJournalEvent
                or type(claim_event.payload) is not dict
                or set(claim_event.payload) != set(claim_payload) | {"updated_at_ns"}
                or type(claim_event.payload.get("updated_at_ns")) is not int
                or claim_event.payload["updated_at_ns"] <= 0
                or {
                    key: value
                    for key, value in claim_event.payload.items()
                    if key != "updated_at_ns"
                }
                != claim_payload
            ):
                raise CtpRiskDispatchError("RISK_CLAIM_EVENT_INVALID")
            claim_event_updated_at_ns = claim_event.payload["updated_at_ns"]
        except Exception:
            # Claim status may already be durable. Keep the local latch closed;
            # a retry can never issue another Risk claim.
            state.state = "CLAIMED_UNJOURNALED"
            raise
        state.permit = permit
        state.claim = claim
        state.claim_event = claim_event
        state.claim_event_updated_at_ns = claim_event_updated_at_ns
        state.state = "CLAIMED"
        return permit

    def settle(self, permit_reference: str) -> RiskPermit:
        self._assert_open_thread()
        state = self._permits.get(permit_reference)
        if state is not None and state.state in {"SETTLED", "ACKED_TRACKED"}:
            return self.ensure_settled(permit_reference)
        if state is None or state.state not in {"CLAIMED", "DISPATCHING"}:
            raise CtpRiskDispatchError("RISK_PERMIT_NOT_CLAIMED")
        try:
            permit = self._adapter.settle(permit_reference)
        except Exception:
            raise CtpRiskDispatchError("RISK_PERMIT_SETTLEMENT_FAILED") from None
        if type(permit) is not RiskPermit or permit.permit_id != permit_reference:
            raise CtpRiskDispatchError("RISK_PERMIT_INVALID")
        state.state = "SETTLED"
        return permit

    def ensure_settled(self, permit_reference: str) -> RiskPermit:
        """Delegate the exact permit's existing Risk recovery-settlement read."""

        self._assert_open_thread()
        state = self._permits.get(permit_reference)
        if state is None or state.state not in {
            "CLAIMED",
            "DISPATCHING",
            "SETTLED",
            "ACKED_TRACKED",
            "RISK_RESOLUTION_UNCERTAIN",
        }:
            raise CtpRiskDispatchError("RISK_PERMIT_NOT_CLAIMED")
        try:
            permit = self._adapter.ensure_settled(permit_reference)
        except Exception:
            raise CtpRiskDispatchError("RISK_PERMIT_RECOVERY_SETTLEMENT_FAILED") from None
        if type(permit) is not RiskPermit or permit.permit_id != permit_reference:
            raise CtpRiskDispatchError("RISK_PERMIT_INVALID")
        if state.state not in {"ACKED_TRACKED", "RISK_RESOLUTION_UNCERTAIN"}:
            state.state = "SETTLED"
        return permit

    def release(self, permit_reference: str, reason: str) -> None:
        self._assert_open_thread()
        state = self._permits.get(permit_reference)
        if state is not None and state.state in {
            "CLAIMING",
            "CLAIMED",
            "CLAIMED_UNJOURNALED",
            "DISPATCHING",
            "DISPATCHING_UNCERTAIN",
            "SETTLED",
            "ACKED_TRACKED",
            "RISK_RESOLUTION_UNCERTAIN",
        }:
            # Delegate so DurableRiskGate enforces its claimed-permit guard.
            self._adapter.release(permit_reference, reason)
            return
        self._adapter.release(permit_reference, reason)
        if state is not None:
            state.state = "RELEASED"

    def before_native_dispatch(
        self,
        *,
        owner: object,
        prepared: Any,
        command: Any,
        reservation: Any,
        dispatch_binding: CtpManagedDispatchBinding,
    ) -> None:
        """Persist the exact queued CTP command binding before Store claim."""

        self._assert_open_thread()
        binding = dispatch_binding
        if owner is not self._require_owner() or type(binding) is not CtpManagedDispatchBinding:
            raise CtpRiskDispatchError("CTP_OWNER_BINDING_MISMATCH")
        correlation = getattr(command, "correlation_key", None)
        native_request_id = getattr(correlation, "native_request_id", None)
        if (
            getattr(prepared, "operation", None) != "submit"
            or getattr(command, "operation", None) != "SUBMIT"
            or getattr(command, "status", None) != "READY"
            or getattr(command, "local_queue_receipt_queued", None) is not True
            or getattr(command, "command_id", None) != binding.command_id
            or getattr(command, "command_id", None) in self._by_command
            or binding.managed_intent_id != prepared.managed_intent_id
            or binding.account_key != owner._scope.account_key
            or binding.scope_key != owner._scope.key
            or binding.trading_day != owner._scope.trading_day
            or reservation.managed_intent_id != binding.managed_intent_id
            or reservation.account_key != binding.account_key
            or reservation.scope_key != binding.scope_key
            or reservation.runtime_order_id != binding.runtime_order_id
            or reservation.order_ref != binding.order_ref
            or owner._session_binding.session_generation_id != binding.session_generation_id
            or owner._session_binding.session_binding_sha256 != binding.session_binding_sha256
            or binding.local_queue_receipt_id != command.local_queue_receipt_id
            or type(native_request_id) is not int
            or binding.native_request_id != native_request_id
            or binding.request_payload_sha256 != command.request_payload_sha256
            or binding.native_request_payload_sha256 != command.native_request_payload_sha256
        ):
            raise CtpRiskDispatchError("CTP_QUEUED_BINDING_MISMATCH")
        state = next(
            (
                item
                for item in self._permits.values()
                if item.intent.intent_id == binding.managed_intent_id
                and item.state == "CLAIMED"
                and item.claim is not None
            ),
            None,
        )
        if state is None:
            raise CtpRiskDispatchError("RISK_CLAIM_UNAVAILABLE")
        self._readback_claim(state)
        if binding.request_payload != dict(command.request_payload):
            raise CtpRiskDispatchError("CTP_REQUEST_PAYLOAD_MISMATCH")
        native_payload = dict(binding.native_request_payload)
        if (
            payload_sha256(native_payload) != binding.native_request_payload_sha256
            or native_payload.get("OrderRef") != binding.order_ref
            or native_payload.get("InstrumentID") != state.intent.instrument
            or native_payload.get("ExchangeID") != self._exchange_name
        ):
            raise CtpRiskDispatchError("CTP_NATIVE_PAYLOAD_MISMATCH")
        try:
            session_payload = owner._store._ctp_callback_session_binding_payload(
                owner._session_binding
            )
            reservation_payload = {
                "account_key": reservation.account_key,
                "created_at_ns": reservation.created_at_ns,
                "managed_intent_id": reservation.managed_intent_id,
                "order_ref": reservation.order_ref,
                "runtime_order_id": reservation.runtime_order_id,
                "scope_key": reservation.scope_key,
                "trading_day": reservation.trading_day,
            }
            reservation_digest = payload_sha256(
                {
                    "registration_type": "ctp_order_identity_registration.v1",
                    "reservation": reservation_payload,
                    "session_binding": session_payload,
                }
            )
            dispatch_payload = {
                "schema": "ctp-risk-ctp-submit-binding-v1",
                "risk_intent_id": state.risk_intent_id,
                "permit_id": state.permit.permit_id,
                "risk_claim_digest": state.claim.claim_digest,
                "risk_intent_hash": state.claim.intent_hash,
                "submit_command_id": binding.command_id,
                "managed_intent_id": binding.managed_intent_id,
                "runtime_order_id": binding.runtime_order_id,
                "order_ref": binding.order_ref,
                "provider_order_id": None,
                "account_key": binding.account_key,
                "scope_key": binding.scope_key,
                "risk_scope_key": self._account_scope.key,
                "trading_day": binding.trading_day,
                "reservation_digest_sha256": reservation_digest,
                "callback_owner_intent_id": owner._owner_handle.owner_intent_id,
                "session_generation_id": binding.session_generation_id,
                "session_binding_sha256": binding.session_binding_sha256,
                "request_payload_sha256": binding.request_payload_sha256,
                "native_request_payload_sha256": binding.native_request_payload_sha256,
                "native_request_id": native_request_id,
                "local_queue_receipt_id": binding.local_queue_receipt_id,
                "instrument_id": native_payload.get("InstrumentID"),
                "exchange_id": native_payload.get("ExchangeID"),
            }
            if (
                not _sha256(reservation_digest)
                or type(dispatch_payload["instrument_id"]) is not str
                or type(dispatch_payload["exchange_id"]) is not str
            ):
                raise CtpRiskDispatchError("CTP_DISPATCH_BINDING_INVALID")
            event = self._journal.append_event(
                self._fence,
                principal_ref=state.principal_ref,
                intent_id=state.intent.intent_id,
                event_type="DISPATCHING",
                payload={
                    "dispatch_attempt_count": 1,
                    "permit_id": state.permit.permit_id,
                    "risk_intent_id": state.risk_intent_id,
                    "ctp_dispatch_binding": dispatch_payload,
                },
            )
            state.dispatch_event = event
            dispatch_readback = self.read_dispatch_binding(
                state.principal_ref, state.intent.intent_id
            )
            if dispatch_readback != dispatch_payload:
                raise CtpRiskDispatchError("DISPATCH_BINDING_READBACK_MISMATCH")
        except Exception:
            state.state = "DISPATCHING_UNCERTAIN"
            raise
        state.state = "DISPATCHING"
        self._by_command[binding.command_id] = state

    def read_dispatch_binding(
        self, principal_ref: str, intent_id: str
    ) -> Mapping[str, Any] | None:
        """Read back the exact immutable DISPATCHING event written by this bridge."""

        self._assert_open_thread()
        if type(principal_ref) is not str or type(intent_id) is not str:
            raise ContractValidationError("Risk journal identity is required")
        selected = next(
            (
                item
                for item in self._permits.values()
                if item.principal_ref == principal_ref and item.intent.intent_id == intent_id
            ),
            None,
        )
        if selected is None or selected.dispatch_event is None:
            return None
        event = self._journal.get_event(
            self._account_scope,
            principal_ref,
            intent_id,
            selected.dispatch_event.revision,
        )
        if (
            type(event) is not RiskOrderJournalEvent
            or event.event_type != "DISPATCHING"
            or event.record_sha256 != selected.dispatch_event.record_sha256
            or event.payload.get("ctp_dispatch_binding")
            != selected.dispatch_event.payload.get("ctp_dispatch_binding")
        ):
            raise CtpRiskDispatchError("DISPATCH_BINDING_READBACK_MISMATCH")
        value = event.payload.get("ctp_dispatch_binding")
        return None if not isinstance(value, Mapping) else dict(value)

    def after_evidenced_observation(
        self,
        record: ExecutionRecord,
        event: ExecutionEvent,
        *,
        writer_lease: WriterLease,
    ) -> None:
        """Resolve only an exact zero-fill ACK into durable tracked exposure.

        The Execution facade calls this after its normal permit settlement.
        CTP callback/query evidence is issued and immediately read back here;
        neither the Execution ACK nor a native queue receipt is enough to
        clear the Risk dispatch latch.
        """

        self._assert_open_thread()
        owner = self._require_owner()
        if (
            type(record) is not ExecutionRecord
            or type(event) is not ExecutionEvent
            or type(writer_lease) is not WriterLease
            or writer_lease is not owner._writer_lease
        ):
            raise CtpRiskDispatchError("EXECUTION_OBSERVATION_BINDING_INVALID")
        owner._assert_worker_thread()
        owner._assert_active_owner()
        owner._store.assert_writer_lease(owner._scope, writer_lease)
        intent = self._read_exact_execution_event(owner, record, event)
        state = self._permits.get(record.permit_reference or "")
        if (
            state is None
            or not _same_order_intent(state.intent, intent)
            or state.permit.permit_id != record.permit_reference
            or state.claim is None
            or state.dispatch_event is None
        ):
            raise CtpRiskDispatchError("RISK_PERMIT_IDENTITY_MISMATCH")

        if state.state == "ACKED_TRACKED":
            if state.tracked_source_event == event:
                return
            # Later positive fills need a distinct fee-complete Risk exposure
            # proof. The zero-fill reader cannot authorize that transition.
            if (
                event.event_type in {"provider_observation", "reconciled_observation"}
                and event.intent_id == intent.intent_id
                and event.scope_key == intent.scope.key
                and event.state in {
                    ExecutionState.PARTIALLY_FILLED,
                    ExecutionState.FILLED,
                }
                and record.state is event.state
                and record.filled_quantity > 0
                and type(record.provider_order_id) is str
                and state.tracked_proof is not None
                and record.provider_order_id == state.tracked_proof.provider_order_id
            ):
                # The facade will persist one review latch for this exact
                # source event. Runtime may continue callback-only maintenance
                # only after this bridge can read that latch and the same
                # tracked Risk receipt back from their durable stores.
                state.tracked_update_review_source_event = event
            raise CtpRiskDispatchError("CTP_TRACKED_ORDER_UPDATE_REQUIRES_REVIEW")
        if state.state == "RISK_RESOLUTION_UNCERTAIN":
            raise CtpRiskDispatchError("RISK_RESOLUTION_REQUIRES_REVIEW")
        if state.state != "SETTLED":
            raise CtpRiskDispatchError("RISK_PERMIT_NOT_SETTLED")
        if (
            event.event_type not in {"provider_observation", "reconciled_observation"}
            or event.intent_id != intent.intent_id
            or event.scope_key != intent.scope.key
            or event.state is not ExecutionState.ACKED
            or record.state is not ExecutionState.ACKED
            or record.filled_quantity != 0
            or record.average_price is not None
            or record.cumulative_commission is not None
            or record.review_required
            or type(record.provider_order_id) is not str
            or not record.provider_order_id
        ):
            raise CtpRiskDispatchError("CTP_ZERO_FILL_ACK_REQUIRED")

        claim = state.claim
        if type(claim) is not DispatchClaimBinding:
            raise CtpRiskDispatchError("RISK_CLAIM_UNAVAILABLE")
        self._readback_claim(state, expected_state="SETTLED")
        dispatch = self.read_dispatch_binding(state.principal_ref, state.intent.intent_id)
        if (
            type(dispatch) is not dict
            or dispatch.get("permit_id") != state.permit.permit_id
            or dispatch.get("risk_intent_id") != state.risk_intent_id
            or dispatch.get("risk_claim_digest") != claim.claim_digest
        ):
            raise CtpRiskDispatchError("DISPATCH_BINDING_READBACK_MISMATCH")
        command = self._journal.get_command(
            self._account_scope, state.principal_ref, state.intent.intent_id
        )
        if (
            command is None
            or command.state != "DISPATCHING"
            or command.permit_id != state.permit.permit_id
            or command.risk_intent_id != state.risk_intent_id
            or not _sha256(command.order_request_sha256)
        ):
            raise CtpRiskDispatchError("RISK_DISPATCH_NOT_READY")

        from bt_api_py.ctp_zero_fill_tracked_order import (
            CtpZeroFillTrackedOrderEvidenceV1,
            CtpZeroFillTrackedOrderReader,
        )
        from bt_api_execution import CtpOwnerCallbackObservationNotReadyV1

        reader = CtpZeroFillTrackedOrderReader(owner)
        state.zero_fill_reader = reader
        # From here onward, any failed journal/gate step is one-shot uncertain.
        # Do not issue a second source query or replay a possibly committed
        # Risk resolution automatically.
        state.state = "RISK_RESOLUTION_UNCERTAIN"
        try:
            evidence = reader.issue(intent.intent_id)
            if type(evidence) is CtpOwnerCallbackObservationNotReadyV1:
                raise CtpRiskDispatchError("CTP_ZERO_FILL_EVIDENCE_UNAVAILABLE")
            if type(evidence) is not CtpZeroFillTrackedOrderEvidenceV1:
                raise CtpRiskDispatchError("CTP_ZERO_FILL_EVIDENCE_INVALID")
            state.zero_fill_evidence = evidence
            if reader.readback(evidence) is not evidence:
                raise CtpRiskDispatchError("CTP_ZERO_FILL_READBACK_MISMATCH")
            owner._assert_active_owner()
            owner._store.assert_writer_lease(owner._scope, writer_lease)
            if not _same_order_intent(
                self._read_exact_execution_event(owner, record, event), intent
            ):
                raise CtpRiskDispatchError("EXECUTION_OBSERVATION_BINDING_INVALID")
            if (
                evidence.scope != intent.scope
                or evidence.managed_intent_id != intent.intent_id
                or evidence.intent_fingerprint != intent.fingerprint
                or evidence.provider_order_id != record.provider_order_id
                or evidence.submit_command_id != dispatch.get("submit_command_id")
                or evidence.managed_intent_id != dispatch.get("managed_intent_id")
                or evidence.local_queue_receipt_id != dispatch.get("local_queue_receipt_id")
                or evidence.native_request_id != dispatch.get("native_request_id")
                or evidence.session_binding.session_generation_id
                != dispatch.get("session_generation_id")
                or evidence.session_binding.session_binding_sha256
                != dispatch.get("session_binding_sha256")
                or evidence.projection.account_key != dispatch.get("account_key")
                or evidence.projection.scope_key != dispatch.get("scope_key")
                or evidence.projection.trading_day != dispatch.get("trading_day")
                or evidence.projection.runtime_order_id != dispatch.get("runtime_order_id")
                or evidence.projection.order_ref != dispatch.get("order_ref")
                or evidence.projection.registration_digest
                != dispatch.get("reservation_digest_sha256")
                or evidence.projection.instrument_id != dispatch.get("instrument_id")
                or evidence.projection.exchange_id != dispatch.get("exchange_id")
                or evidence.quantity != intent.quantity
                or evidence.projection.quantity != evidence.quantity
                or evidence.callback_snapshot.owner_intent_id
                != dispatch.get("callback_owner_intent_id")
                or evidence.callback_snapshot.session_generation_id
                != dispatch.get("session_generation_id")
                or evidence.provider_observation.state is not ExecutionState.ACKED
                or evidence.provider_observation.filled_quantity != 0
                or evidence.projection.provider_state != "OPEN"
                or evidence.projection.traded_quantity != 0
                or evidence.projection.remaining_quantity != evidence.quantity
                or evidence.callback_snapshot.trade_notional != 0
                or evidence.callback_snapshot.trade_fact_count != 0
                or evidence.callback_snapshot.provider_state != "ACKNOWLEDGED"
                or evidence.callback_snapshot.filled_quantity != 0
            ):
                raise CtpRiskDispatchError("CTP_ZERO_FILL_EVIDENCE_MISMATCH")

            evidence_payload = evidence.to_payload()
            evidence_sha256 = evidence.evidence_sha256
            # RiskOrderJournal's public observation envelope has a closed
            # top-level allowlist. Keep the CTP-specific current-projection
            # evidence in its bounded `record` member rather than inventing
            # new envelope keys or implying a complete GetDeals history.
            ctp_observation_record = {
                "schema": "ctp-risk-current-order-evidence-v2",
                "evidence_kind": "CTP_CALLBACK_QUERY_ORDER_AND_QUERY_TRADE_TARGET_ORDER_WINDOW",
                "evidence_sha256": evidence_sha256,
                "evidence": evidence_payload,
                "execution_intent_id": intent.intent_id,
                "execution_intent_sha256": intent.fingerprint,
                "risk_intent_id": state.risk_intent_id,
                "observation_scope": "CURRENT_ORDER_AND_TRADE_QUERY_WINDOW",
                "fill_history_completeness": "COMPLETE_FOR_TARGET_ORDER",
                "trade_history_observed": True,
                "trade_fact_count": evidence.callback_snapshot.trade_fact_count,
                "trade_notional": format(evidence.callback_snapshot.trade_notional, "f"),
                "cumulative_commission": None,
                "cumulative_commission_currency": None,
                "commission_quality": "INCOMPLETE",
            }
            tracked_order = {
                "account_id": self._account_scope.account_id,
                "claim_digest": claim.claim_digest,
                "instrument": intent.instrument,
                "position_bucket": intent.position_bucket,
                "position_effect": intent.position_effect.value,
                "position_side": intent.position_side,
                "provider_order_id": evidence.provider_order_id,
                "quantity": format(intent.quantity, "f"),
                "reduce_only": intent.reduce_only,
                "record": {
                    **ctp_observation_record,
                    "provider_state": "OPEN",
                    "provider_status": "ACKED",
                },
                "schema": "ctp-risk-current-order-observation-v1",
                "side": intent.side.value,
                "source_alignment_valid": True,
                "status": "OPEN",
                "filled_quantity": "0",
                "trade_count": 0,
            }
            tracked_fills = {
                "account_id": self._account_scope.account_id,
                "claim_digest": claim.claim_digest,
                "filled_quantity": "0",
                "instrument": intent.instrument,
                "provider_order_id": evidence.provider_order_id,
                "record": {
                    **ctp_observation_record,
                    "requested_quantity": format(intent.quantity, "f"),
                },
                "risk_intent_id": state.risk_intent_id,
                "schema": "ctp-risk-current-order-zero-fill-observation-v1",
                "source_alignment_valid": True,
                "trade_count": 0,
            }
            order_event, fill_event, reconciliation_sha = (
                self._journal.append_resolution_observations(
                    self._fence,
                    principal_ref=state.principal_ref,
                    intent_id=intent.intent_id,
                    order_observation=tracked_order,
                    fill_observation=tracked_fills,
                )
            )
            expected_order_event_payload = {
                **tracked_order,
                "updated_at_ns": order_event.payload.get("updated_at_ns")
                if type(order_event) is RiskOrderJournalEvent
                and type(order_event.payload) is dict
                else None,
            }
            expected_fill_event_payload = {
                **tracked_fills,
                "updated_at_ns": fill_event.payload.get("updated_at_ns")
                if type(fill_event) is RiskOrderJournalEvent
                and type(fill_event.payload) is dict
                else None,
            }
            if (
                type(order_event) is not RiskOrderJournalEvent
                or type(fill_event) is not RiskOrderJournalEvent
                or order_event.event_type != "ORDER_OBSERVATION"
                or fill_event.event_type != "FILL_OBSERVATION"
                or order_event.revision >= fill_event.revision
                or type(order_event.payload) is not dict
                or set(order_event.payload) != set(expected_order_event_payload)
                or risk_canonical_json(order_event.payload)
                != risk_canonical_json(expected_order_event_payload)
                or type(order_event.payload.get("updated_at_ns")) is not int
                or order_event.payload["updated_at_ns"] <= 0
                or type(fill_event.payload) is not dict
                or set(fill_event.payload) != set(expected_fill_event_payload)
                or risk_canonical_json(fill_event.payload)
                != risk_canonical_json(expected_fill_event_payload)
                or type(fill_event.payload.get("updated_at_ns")) is not int
                or fill_event.payload["updated_at_ns"] <= 0
                or self._journal.get_event(
                    self._account_scope,
                    state.principal_ref,
                    intent.intent_id,
                    order_event.revision,
                )
                != order_event
                or self._journal.get_event(
                    self._account_scope,
                    state.principal_ref,
                    intent.intent_id,
                    fill_event.revision,
                )
                != fill_event
            ):
                raise CtpRiskDispatchError("RISK_OBSERVATION_READBACK_MISMATCH")
            settled_permit = self._adapter.ensure_settled(state.permit.permit_id)
            if (
                type(settled_permit) is not RiskPermit
                or settled_permit.permit_id != state.permit.permit_id
            ):
                raise CtpRiskDispatchError("RISK_PERMIT_SETTLEMENT_READBACK_INVALID")
            reservation_id, reservation_sha, _reference = self._journal.store_exposure_reference(
                self._fence,
                principal_ref=state.principal_ref,
                intent_id=intent.intent_id,
                claim=claim,
                evidence_class=_evidence_class(self._account_scope),
                permit_id=state.permit.permit_id,
                provider_order_id=evidence.provider_order_id,
                accepted_request_sha256=command.order_request_sha256,
                reconciliation_evidence_sha256=reconciliation_sha,
                observation_revision=fill_event.revision,
                observation_record_sha256=fill_event.record_sha256,
            )
            proof_command = self._journal.get_command(
                self._account_scope, state.principal_ref, intent.intent_id
            )
            if proof_command is None or proof_command.state != "PROOF_READY":
                raise CtpRiskDispatchError("RISK_PROOF_READY_MISSING")
            proof = DispatchTrackedOrderProof(
                scope=claim.scope,
                permit_id=claim.permit_id,
                intent_id=claim.intent_id,
                intent_hash=claim.intent_hash,
                cause_id=claim.cause_id,
                claim_digest=claim.claim_digest,
                evidence_class=_evidence_class(self._account_scope),
                dispatch_attempt_count=1,
                journal_revision=proof_command.latest_revision,
                journal_record_sha256=proof_command.latest_record_sha256,
                reconciliation_evidence_sha256=reconciliation_sha,
                writer_fence_sha256=self._fence.fence_sha256,
                provider_order_id=evidence.provider_order_id,
                accepted_request_sha256=command.order_request_sha256,
                exposure_reservation_id=reservation_id,
                exposure_reservation_sha256=reservation_sha,
                filled_quantity=0,
                trade_count=0,
            )
            if reader.readback(evidence) is not evidence:
                raise CtpRiskDispatchError("CTP_ZERO_FILL_READBACK_MISMATCH")
            if (
                not _same_order_intent(
                    self._read_exact_execution_event(owner, record, event), intent
                )
                or self._journal.get_command(
                    self._account_scope, state.principal_ref, intent.intent_id
                ).state
                != "PROOF_READY"
            ):
                raise CtpRiskDispatchError("RISK_PROOF_SOURCE_CHANGED")
            gate_receipt = self._gate.resolve_dispatch_freeze(proof)
            if type(gate_receipt) is not DispatchResolutionReceipt:
                gate_receipt = self._gate.dispatch_resolution_receipt(state.permit.permit_id)
            if (
                type(gate_receipt) is not DispatchResolutionReceipt
                or gate_receipt.status is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
                or gate_receipt.claim != claim
                or gate_receipt.proof != proof
                or gate_receipt.proof_sha256 != proof.fingerprint
                or gate_receipt.dispatch_latch_active is not False
            ):
                raise CtpRiskDispatchError("RISK_TRACKED_RECEIPT_INVALID")
            tracked_event = self._journal.append_event(
                self._fence,
                principal_ref=state.principal_ref,
                intent_id=intent.intent_id,
                event_type="ACKED_TRACKED",
                payload={
                    "claim_digest": claim.claim_digest,
                    "dispatch_attempt_count": 1,
                    "exposure_reference_id": reservation_id,
                    "exposure_reference_sha256": reservation_sha,
                    "filled_quantity": 0,
                    "permit_id": state.permit.permit_id,
                    "proof_ready_record_sha256": proof.journal_record_sha256,
                    "proof_ready_revision": proof.journal_revision,
                    "proof_sha256": proof.fingerprint,
                    "provider_order_id": evidence.provider_order_id,
                    "public_state": "ACKED_TRACKED",
                    "recovery_attestation_sha256": None,
                    "reconciliation_evidence_sha256": reconciliation_sha,
                    "risk_intent_id": claim.intent_id,
                    "trade_count": 0,
                },
            )
            final_command = self._journal.get_command(
                self._account_scope, state.principal_ref, intent.intent_id
            )
            final_receipt = self._gate.dispatch_resolution_receipt(state.permit.permit_id)
            if (
                final_command is None
                or final_command.state != "ACKED_TRACKED"
                or type(final_receipt) is not DispatchResolutionReceipt
                or final_receipt != gate_receipt
                or not self._journal.has_resolved_ack_history(
                    self._fence,
                    principal_ref=state.principal_ref,
                    intent_id=intent.intent_id,
                    receipt=final_receipt,
                )
                or self._journal.get_event(
                    self._account_scope,
                    state.principal_ref,
                    intent.intent_id,
                    tracked_event.revision,
                )
                != tracked_event
            ):
                raise CtpRiskDispatchError("RISK_TRACKED_READBACK_MISMATCH")
            state.tracked_proof = proof
            state.tracked_receipt = final_receipt
            state.tracked_event = tracked_event
            state.tracked_source_event = event
            state.state = "ACKED_TRACKED"
        except Exception:
            state.state = "RISK_RESOLUTION_UNCERTAIN"
            raise

    def read_tracked_update_review(
        self,
        record: ExecutionRecord,
        *,
        writer_lease: WriterLease,
    ) -> ExecutionEvent:
        """Read back this bridge's exact positive-fill review latch.

        This narrow capability does not resolve or release Risk exposure. It
        permits the runtime to keep ingesting callback-backed facts for an
        ACKED_TRACKED order while its full reservation remains counted.
        """

        self._assert_open_thread()
        owner = self._require_owner()
        if (
            type(record) is not ExecutionRecord
            or type(writer_lease) is not WriterLease
            or writer_lease is not owner._writer_lease
        ):
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_BINDING_INVALID")
        owner._assert_worker_thread()
        owner._assert_active_owner()
        owner._store.assert_writer_lease(owner._scope, writer_lease)
        state = self._permits.get(record.permit_reference or "")
        source_event = (
            None if state is None else state.tracked_update_review_source_event
        )
        if (
            state is None
            or state.state != "ACKED_TRACKED"
            or type(source_event) is not ExecutionEvent
            or type(state.tracked_proof) is not DispatchTrackedOrderProof
            or type(state.tracked_receipt) is not DispatchResolutionReceipt
            or type(state.tracked_event) is not RiskOrderJournalEvent
            or record.scope_key != owner._scope.key
            or record.intent_id != state.intent.intent_id
            or record.payload_sha256 != state.intent.fingerprint
            or record.permit_reference != state.permit.permit_id
            or record.review_required is not True
            or record.state not in {
                ExecutionState.PARTIALLY_FILLED,
                ExecutionState.FILLED,
            }
            or record.filled_quantity <= 0
            or record.provider_order_id != state.tracked_proof.provider_order_id
            or source_event.event_type
            not in {"provider_observation", "reconciled_observation"}
            or source_event.intent_id != record.intent_id
            or source_event.scope_key != record.scope_key
            or source_event.state is not record.state
        ):
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_BINDING_INVALID")
        try:
            intent = self._read_exact_execution_event(owner, record, source_event)
            events = owner._store.read_outbox(
                after_sequence=source_event.sequence - 1,
                limit=2,
                scope=owner._scope,
            )
        except Exception:
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_READBACK_FAILED") from None
        if (
            not _same_order_intent(state.intent, intent)
            or len(events) != 2
            or events[0] != source_event
        ):
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_READBACK_MISMATCH")
        review_event = events[1]
        if (
            type(review_event) is not ExecutionEvent
            or review_event.sequence != source_event.sequence + 1
            or review_event.intent_id != record.intent_id
            or review_event.scope_key != record.scope_key
            or review_event.event_type != "manual_review_required"
            or review_event.state is not record.state
            or dict(review_event.payload)
            != {"reason_code": "evidenced_observation_reconciliation_failed"}
            or owner._store.get(record.intent_id, scope=owner._scope) != record
        ):
            raise CtpRiskDispatchError("TRACKED_UPDATE_REVIEW_READBACK_MISMATCH")

        proof = state.tracked_proof
        receipt = self._gate.dispatch_resolution_receipt(state.permit.permit_id)
        command = self._journal.get_command(
            self._account_scope, state.principal_ref, state.intent.intent_id
        )
        tracked_event = self._journal.get_event(
            self._account_scope,
            state.principal_ref,
            state.intent.intent_id,
            state.tracked_event.revision,
        )
        if (
            type(receipt) is not DispatchResolutionReceipt
            or receipt != state.tracked_receipt
            or receipt.status
            is not DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
            or receipt.dispatch_latch_active is not False
            or receipt.proof != proof
            or receipt.proof_sha256 != proof.fingerprint
            or command is None
            or command.state != "ACKED_TRACKED"
            or command.permit_id != state.permit.permit_id
            or command.risk_intent_id != state.risk_intent_id
            or tracked_event != state.tracked_event
            or tracked_event.event_type != "ACKED_TRACKED"
            or not self._journal.has_resolved_ack_history(
                self._fence,
                principal_ref=state.principal_ref,
                intent_id=state.intent.intent_id,
                receipt=receipt,
            )
        ):
            raise CtpRiskDispatchError("TRACKED_RISK_RECEIPT_READBACK_MISMATCH")
        return review_event

    def _read_exact_execution_event(
        self, owner: Any, record: ExecutionRecord, event: ExecutionEvent
    ) -> OrderIntent:
        """Bind callback arguments to the current single-event outbox read."""

        page = owner._store.read_execution_outbox_page(
            (owner._scope,), after_sequence=event.sequence - 1, limit=1
        )
        if page.events != (event,):
            raise CtpRiskDispatchError("EXECUTION_EVENT_READBACK_MISMATCH")
        matching = tuple(
            item
            for item in page.intents
            if item.record.intent_id == record.intent_id
        )
        if (
            len(matching) != 1
            or matching[0].record != record
            or matching[0].intent.scope != owner._scope
            or matching[0].intent.intent_id != record.intent_id
            or matching[0].intent.fingerprint != record.payload_sha256
        ):
            raise CtpRiskDispatchError("EXECUTION_RECORD_READBACK_MISMATCH")
        return matching[0].intent

    def close(self) -> None:
        """Release only this bridge's account journal fence on its owner thread."""

        self._assert_thread()
        if self._closed:
            return
        if self._close_uncertain:
            raise CtpRiskDispatchError("RISK_WRITER_FENCE_CLOSE_UNCERTAIN")
        if self._fence_cm is None:
            raise CtpRiskDispatchError("RISK_WRITER_FENCE_UNAVAILABLE")
        try:
            self._fence_cm.__exit__(None, None, None)
        except Exception:
            # A generator context may already be exhausted after a failing
            # exit. A second empty exit is not evidence that its lock released.
            self._close_uncertain = True
            raise CtpRiskDispatchError("RISK_WRITER_FENCE_CLOSE_UNCERTAIN") from None
        self._closed = True
        self._fence_cm = None

    def _wire_scope_payload(self, context: ExecutionAdmissionContextV1) -> dict[str, Any]:
        scope = context.to_payload().get("wire_scope")
        if type(scope) is not dict:
            raise CtpRiskDispatchError("WIRE_SCOPE_INVALID")
        return scope

    def _order_request(self, intent: OrderIntent, mapped: RiskIntent) -> dict[str, Any]:
        side = BtSide(intent.side.value.lower())
        order_type = BtOrderType(intent.order_type.value.lower())
        offset = {
            ("OPEN", "ALL"): "open",
            ("CLOSE", "ALL"): "close",
            ("CLOSE_TODAY", "TODAY"): "close_today",
            ("CLOSE_YESTERDAY", "YESTERDAY"): "close_yesterday",
        }.get((intent.position_effect.value, intent.position_bucket))
        if offset is None:
            raise CtpRiskDispatchError("CTP_POSITION_EFFECT_INVALID")
        request = OrderRequest(
            symbol=intent.instrument,
            side=side,
            order_type=order_type,
            quantity=intent.quantity,
            account_id=self._account_scope.account_id,
            client_order_id=intent.intent_id,
            price=intent.price,
            time_in_force=intent.time_in_force.value,
            reduce_only=intent.reduce_only,
            idempotency_key=intent.intent_id,
            quantity_unit=mapped.quantity_unit,
            position_side=intent.position_side,
            offset=offset,
        )
        return request.to_dict()

    def _claim_payload(self, claim: DispatchClaimBinding) -> dict[str, Any]:
        return {
            "cause_id": claim.cause_id,
            "claim_digest": claim.claim_digest,
            "claimed_at": claim.claimed_at,
            "intent_hash": claim.intent_hash,
            "intent_id": claim.intent_id,
            "permit_id": claim.permit_id,
            "scope_key": claim.scope.key,
        }

    def _readback_claim(
        self, state: _PermitState, *, expected_state: str = "CLAIMED"
    ) -> None:
        """Recheck the current Risk latch and its exact durable CLAIMED event."""

        claim = state.claim
        event_written = state.claim_event
        if (
            state.state != expected_state
            or claim is None
            or type(event_written) is not RiskOrderJournalEvent
            or not _sha256(event_written.record_sha256)
        ):
            raise CtpRiskDispatchError("RISK_CLAIM_UNAVAILABLE")
        try:
            current_claim = self._gate.dispatch_claim_binding(state.permit.permit_id)
            event = self._journal.get_event(
                self._account_scope,
                state.principal_ref,
                state.intent.intent_id,
                event_written.revision,
            )
        except Exception:
            raise CtpRiskDispatchError("RISK_CLAIM_READBACK_FAILED") from None
        expected_payload = {
            "claim": self._claim_payload(claim),
            "permit_id": state.permit.permit_id,
            "risk_intent_id": state.risk_intent_id,
        }
        updated_at_ns = state.claim_event_updated_at_ns
        if type(updated_at_ns) is not int or updated_at_ns <= 0:
            raise CtpRiskDispatchError("RISK_CLAIM_READBACK_MISMATCH")
        expected_event_payload = {
            **expected_payload,
            "updated_at_ns": updated_at_ns,
        }
        if (
            type(current_claim) is not DispatchClaimBinding
            or current_claim != claim
            or type(event) is not RiskOrderJournalEvent
            or event.scope != self._account_scope
            or event.principal_ref != state.principal_ref
            or event.intent_id != state.intent.intent_id
            or event.revision != event_written.revision
            or event.event_type != "CLAIMED"
            or type(event_written.payload) is not dict
            or set(event_written.payload) != set(expected_event_payload)
            or event_written.payload != expected_event_payload
            or type(event.payload) is not dict
            or set(event.payload) != set(expected_event_payload)
            or event.payload != event_written.payload
            or event.payload != expected_event_payload
            or event.record_sha256 != event_written.record_sha256
            or event.writer_owner_id != self._fence.owner_id
            or event.writer_fencing_token != self._fence.fencing_token
            or event.writer_fence_sha256 != self._fence.fence_sha256
        ):
            raise CtpRiskDispatchError("RISK_CLAIM_READBACK_MISMATCH")

    def _require_permit(self, permit_reference: str, intent: OrderIntent) -> _PermitState:
        if type(permit_reference) is not str or type(intent) is not OrderIntent:
            raise CtpRiskDispatchError("RISK_PERMIT_IDENTITY_INVALID")
        state = self._permits.get(permit_reference)
        if (
            state is None
            or state.intent is not intent
            or state.intent.fingerprint != intent.fingerprint
            or state.risk_intent.intent_id != intent.intent_id
        ):
            raise CtpRiskDispatchError("RISK_PERMIT_IDENTITY_MISMATCH")
        return state

    def _require_owner(self) -> Any:
        if self._owner is None:
            raise CtpRiskDispatchError("CTP_OWNER_UNBOUND")
        return self._owner

    def _assert_thread(self) -> None:
        if current_thread() is not self._thread:
            raise ContractValidationError("CTP Risk bridge is confined to its creating thread")

    def _assert_open_thread(self) -> None:
        self._assert_thread()
        if self._closed:
            raise CtpRiskDispatchError("CTP_RISK_BRIDGE_CLOSED")


def _sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _same_order_intent(left: object, right: object) -> bool:
    """Compare Store-reconstructed intents by exact typed durable value."""

    return (
        type(left) is OrderIntent
        and type(right) is OrderIntent
        and left.fingerprint == right.fingerprint
        and left.to_payload() == right.to_payload()
    )


def _evidence_class(scope: AccountScope) -> DispatchEvidenceClass:
    return (
        DispatchEvidenceClass.SIMULATION_JOURNAL
        if scope.provider.casefold() in {"fake", "fixture"}
        else DispatchEvidenceClass.NATIVE_PROVIDER_JOURNAL
    )


__all__ = ["CtpRiskDispatchBridge", "CtpRiskDispatchError"]
