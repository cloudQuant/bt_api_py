"""Bind one authenticated Execution admission to the managed CTP outbox.

This first bridge slice records the already-created Risk reservation and its
single dispatch claim before the CTP Store lets a native call proceed. It does
not resolve a claimed permit from callback or query evidence; until that
separate proof path is installed, every terminal settlement remains blocked.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from threading import current_thread
from typing import Any

from bt_api_execution import (
    ContractValidationError,
    ExecutionScope,
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


class CtpRiskDispatchBridge:
    """Join trusted Execution admission, one Risk claim, and one CTP command.

    ``adapter`` and ``journal`` are supplied by trusted service composition.
    The bridge is bound to the exact live CTP owner after its submit port has
    bound BasicAdmission. All methods that touch the journal or owner are
    confined to that same thread. It never mints a Risk scope, maps a caller
    claim, or treats a native queue result as terminal evidence.
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
        if state is None or state.state not in {"CLAIMED", "DISPATCHING", "SETTLED"}:
            raise CtpRiskDispatchError("RISK_PERMIT_NOT_CLAIMED")
        try:
            permit = self._adapter.ensure_settled(permit_reference)
        except Exception:
            raise CtpRiskDispatchError("RISK_PERMIT_RECOVERY_SETTLEMENT_FAILED") from None
        if type(permit) is not RiskPermit or permit.permit_id != permit_reference:
            raise CtpRiskDispatchError("RISK_PERMIT_INVALID")
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

    def _readback_claim(self, state: _PermitState) -> None:
        """Recheck the current Risk latch and its exact durable CLAIMED event."""

        claim = state.claim
        event_written = state.claim_event
        if (
            state.state != "CLAIMED"
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


__all__ = ["CtpRiskDispatchBridge", "CtpRiskDispatchError"]
