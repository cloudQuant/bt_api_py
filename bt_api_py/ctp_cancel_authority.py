"""One-shot Risk and Store-bound authority for a managed CTP cancellation.

The issuer borrows an already ACTIVE :class:`CtpExecutionOwner`; it does not
own a TraderClient/native lifecycle and never sends a request.  A Risk claim
receipt is only read-only evidence of the cancellation latch.  Native
authority is returned only for the exact durable queued CANCEL after the
Store has rechecked the same reservation, query target, session, writer lease,
and one-use approval binding.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from bt_api_execution import (
    CancelDispatchClaimReceiptV1,
    CancelIntent,
    CancelRecord,
    ContractValidationError,
    CtpCallbackSessionBindingV1,
    CtpDispatchAuthority,
    CtpDispatchCommand,
    CtpDispatchCorrelationKey,
    CtpOrderIdentityReservation,
    CtpOrderTargetProjectionHandle,
    CtpOrderTargetQueryProvenance,
    CtpVerifiedOrderTargetProjection,
    ExecutionScope,
    ExecutionState,
    InvalidStateTransition,
    OrderIntent,
    SqliteExecutionStore,
    WriterLease,
    WriterLeaseUnavailable,
    payload_sha256,
)
from bt_api_execution.ctp_single_worker_candidate import (
    CtpManagedDispatchBinding,
    CtpManagedPreparedDispatch,
    stable_managed_command_id,
)
from bt_api_risk import DispatchClaimBinding

from bt_api_py.service_execution_risk_cancel import (
    RiskExecutionCancelAdmissionError,
    RiskExecutionCancelAdmissionGate,
)


_AUTHORITY_TYPE = "ctp_dispatch_authority.v1"
_VERIFIER_ID = "bt_api_py.ctp_cancel_authority.v1"
_CANCEL_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$", re.ASCII)
_MAX_TTL_NS = 30_000_000_000
_MAX_PENDING = 256


class CtpCancelAuthorityError(ContractValidationError):
    """Finite fail-closed error codes for the CTP cancellation issuer."""

    _CODES = frozenset(
        {
            "invalid_binding",
            "wrong_thread",
            "owner_unavailable",
            "risk_claim_unavailable",
            "risk_claim_mismatch",
            "invalid_cancel_identity",
            "cancel_record_unavailable",
            "cancel_not_dispatching",
            "target_unavailable",
            "target_identity_mismatch",
            "target_expired",
            "submit_source_unavailable",
            "submit_source_mismatch",
            "invalid_request_identity",
            "pending_capacity_full",
            "pending_authority_exists",
            "pending_authority_missing",
            "pending_authority_expired",
            "pending_authority_used",
            "dispatch_command_mismatch",
        }
    )

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in self._CODES:
            raise ValueError("invalid CTP cancel authority error code")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _CancelFacts:
    record: CancelRecord
    cancel_intent: CancelIntent
    risk_claim: DispatchClaimBinding
    reservation: CtpOrderIdentityReservation
    target_intent: OrderIntent
    submit_command: CtpDispatchCommand
    session_binding: CtpCallbackSessionBindingV1
    session_payload: Mapping[str, Any]
    broker_id: str
    user_id: str
    target: CtpVerifiedOrderTargetProjection
    query_provenance: CtpOrderTargetQueryProvenance


@dataclass(frozen=True, slots=True)
class _PendingCancel:
    cancel_intent: CancelIntent
    cancel_intent_json: str
    claim_receipt: CancelDispatchClaimReceiptV1
    target_projection: CtpOrderTargetProjectionHandle
    prepared: CtpManagedPreparedDispatch
    approval_digest: str
    expires_monotonic_ns: int
    verified: bool = False


def _fail(code: str) -> None:
    raise CtpCancelAuthorityError(code)


def _reservation_payload(reservation: CtpOrderIdentityReservation) -> dict[str, Any]:
    return {
        "account_key": reservation.account_key,
        "trading_day": reservation.trading_day,
        "scope_key": reservation.scope_key,
        "managed_intent_id": reservation.managed_intent_id,
        "runtime_order_id": reservation.runtime_order_id,
        "order_ref": reservation.order_ref,
        "created_at_ns": reservation.created_at_ns,
    }


def _claim_payload(binding: DispatchClaimBinding) -> dict[str, Any]:
    return {
        "permit_id": binding.permit_id,
        "scope": binding.scope.key,
        "intent_id": binding.intent_id,
        "intent_hash": binding.intent_hash,
        "cause_id": binding.cause_id,
        "claimed_at": binding.claimed_at,
        "claim_digest": binding.claim_digest,
    }


def _source_tags(binding: CtpCallbackSessionBindingV1) -> tuple[Any, ...]:
    return (
        binding.source_instance_id,
        binding.native_client_epoch,
        binding.native_api_source_id,
        binding.native_spi_source_id,
        binding.native_api_generation,
        binding.source_connection_generation,
    )


def _cancel_record_payload(record: CancelRecord) -> dict[str, Any]:
    return {
        "cancel_id": record.cancel_id,
        "scope_key": record.scope_key,
        "target_intent_id": record.target_intent_id,
        "provider_order_id": record.provider_order_id,
        "payload_sha256": record.payload_sha256,
        "state": record.state.value,
        "permit_reference": record.permit_reference,
        "dispatch_attempts": record.dispatch_attempts,
        "unknown_reason": record.unknown_reason,
        "review_required": record.review_required,
        "created_at_ns": record.created_at_ns,
        "updated_at_ns": record.updated_at_ns,
    }


class CtpCancelAuthorityIssuerV1:
    """Prepare and freshly verify one claimed CTP CANCEL without sending it.

    This object is tied to one exact borrowed ``CtpExecutionOwner`` and one
    ``RiskExecutionCancelAdmissionGate``.  Risk claim readback is not native
    permission: the Store's final session/writer/projection checks and one-use
    command claim remain authoritative.
    """

    def __init__(
        self,
        owner: object,
        admission_gate: RiskExecutionCancelAdmissionGate,
        *,
        ttl_ns: int = 5_000_000_000,
        max_pending: int = 64,
    ) -> None:
        from bt_api_py.ctp_execution_owner import CtpExecutionOwner

        if type(owner) is not CtpExecutionOwner or type(
            admission_gate
        ) is not RiskExecutionCancelAdmissionGate:
            _fail("invalid_binding")
        if (
            type(ttl_ns) is not int
            or not 1_000_000 <= ttl_ns <= _MAX_TTL_NS
            or type(max_pending) is not int
            or not 1 <= max_pending <= _MAX_PENDING
        ):
            _fail("invalid_binding")
        owner._assert_worker_thread()
        try:
            binding = owner._assert_active_owner()
            owner._store.assert_writer_lease(owner._scope, owner._writer_lease)
        except (InvalidStateTransition, WriterLeaseUnavailable):
            _fail("owner_unavailable")
        scope = owner._scope
        if (
            type(owner._store) is not SqliteExecutionStore
            or type(scope) is not ExecutionScope
            or type(owner._writer_lease) is not WriterLease
            or admission_gate._store is not owner._store
            or admission_gate._execution_scope != scope
            or admission_gate._risk_scope.provider != scope.provider
            or admission_gate._risk_scope.environment != scope.environment
            or admission_gate._wire_scope_template.account_ref != scope.account_ref
            or admission_gate._wire_scope_template.strategy_ref != scope.strategy_id
            or type(binding) is not CtpCallbackSessionBindingV1
            or binding.account_key != scope.account_key
            or binding.scope_key != scope.key
            or binding.trading_day != scope.trading_day
        ):
            _fail("invalid_binding")
        self._owner = owner
        self._store = owner._store
        self._scope = scope
        self._writer_lease = owner._writer_lease
        self._session_adapter = owner._session_adapter
        self._owner_handle = owner._owner_handle
        self._admission_gate = admission_gate
        self._ttl_ns = ttl_ns
        self._max_pending = max_pending
        self._thread = threading.current_thread()
        self._lock = threading.RLock()
        self._pending: dict[str, _PendingCancel] = {}
        self._busy = False

    def prepare_cancel(
        self,
        intent: CancelIntent,
        claim_receipt: CancelDispatchClaimReceiptV1,
        target_projection: CtpOrderTargetProjectionHandle,
        *,
        native_request_id: int,
        local_queue_receipt_id: str,
    ) -> CtpManagedPreparedDispatch:
        """Bind one exact DISPATCHING cancel to Risk, target, session and request IDs."""

        self._begin_call()
        try:
            if (
                type(intent) is not CancelIntent
                or type(claim_receipt) is not CancelDispatchClaimReceiptV1
                or type(target_projection) is not CtpOrderTargetProjectionHandle
            ):
                _fail("invalid_cancel_identity")
            if (
                type(native_request_id) is not int
                or not 1 <= native_request_id <= 2_147_483_647
                or type(local_queue_receipt_id) is not str
            ):
                _fail("invalid_request_identity")
            if (
                _CANCEL_ID.fullmatch(intent.cancel_id) is None
                or intent.cancel_id == intent.target_intent_id
                or intent.scope != self._scope
                or claim_receipt.scope_key != self._scope.key
                or claim_receipt.cancel_id != intent.cancel_id
                or claim_receipt.intent_fingerprint != intent.fingerprint
            ):
                _fail("invalid_cancel_identity")

            start_monotonic_ns = time.monotonic_ns()
            facts = self._read_current_facts(intent, claim_receipt, target_projection)
            prepared, approval_digest = self._build_prepared(
                intent,
                claim_receipt,
                target_projection,
                facts,
                native_request_id=native_request_id,
                local_queue_receipt_id=local_queue_receipt_id,
            )
            expires_monotonic_ns = min(
                start_monotonic_ns + self._ttl_ns,
                facts.target.expires_at_ns,
                facts.query_provenance.expires_at_ns,
            )
            with self._lock:
                now_monotonic_ns = time.monotonic_ns()
                if expires_monotonic_ns <= now_monotonic_ns:
                    _fail("target_expired")
                self._purge_expired_locked(now_monotonic_ns)
                if claim_receipt.permit_reference in self._pending:
                    _fail("pending_authority_exists")
                if len(self._pending) >= self._max_pending:
                    _fail("pending_capacity_full")
                self._pending[claim_receipt.permit_reference] = _PendingCancel(
                    cancel_intent=intent,
                    cancel_intent_json=self._canonical_intent(intent),
                    claim_receipt=claim_receipt,
                    target_projection=target_projection,
                    prepared=prepared,
                    approval_digest=approval_digest,
                    expires_monotonic_ns=expires_monotonic_ns,
                )
            return prepared
        finally:
            self._end_call()

    def verify_action(
        self, command: CtpDispatchCommand, *, now_ns: int
    ) -> CtpDispatchAuthority:
        """Re-read and authorize one exact queued CANCEL for Store's one-use claim."""

        self._begin_call()
        try:
            if type(command) is not CtpDispatchCommand or type(now_ns) is not int or now_ns <= 0:
                _fail("dispatch_command_mismatch")
            if (
                command.operation != "CANCEL"
                or command.status != "READY"
                or command.local_queue_receipt_queued is not True
                or type(command.approval_use_id) is not str
                or not command.approval_use_id
            ):
                _fail("dispatch_command_mismatch")
            permit_reference = command.approval_use_id
            with self._lock:
                pending = self._pending.get(permit_reference)
            if pending is None:
                _fail("pending_authority_missing")
            if pending.verified:
                _fail("pending_authority_used")
            if time.monotonic_ns() >= pending.expires_monotonic_ns:
                _fail("pending_authority_expired")

            # Rebuild every source binding before marking the pending entry
            # used. Invalid command echoes therefore cannot consume a valid
            # issuer slot, while a returned authority remains one-shot.
            facts = self._read_current_facts(
                pending.cancel_intent,
                pending.claim_receipt,
                pending.target_projection,
            )
            expected, approval_digest = self._build_prepared(
                pending.cancel_intent,
                pending.claim_receipt,
                pending.target_projection,
                facts,
                native_request_id=pending.prepared.native_request_id,
                local_queue_receipt_id=pending.prepared.local_queue_receipt_id,
            )
            if (
                expected != pending.prepared
                or approval_digest != pending.approval_digest
                or self._canonical_intent(pending.cancel_intent)
                != pending.cancel_intent_json
            ):
                _fail("dispatch_command_mismatch")
            durable = self._store.read_ctp_dispatch_command(self._scope, command.command_id)
            if type(durable) is not CtpDispatchCommand or durable != command:
                _fail("dispatch_command_mismatch")
            self._validate_command(command, expected, facts)

            remaining_ns = min(
                pending.expires_monotonic_ns,
                facts.target.expires_at_ns,
                facts.query_provenance.expires_at_ns,
            ) - time.monotonic_ns()
            if remaining_ns <= 0:
                _fail("pending_authority_expired")
            self._store.assert_writer_lease(self._scope, self._writer_lease)
            source_digest = payload_sha256(
                {
                    "source_type": "ctp_risk_cancel_authority_source.v1",
                    "cancel_intent": pending.cancel_intent.to_payload(),
                    "cancel_record": _cancel_record_payload(facts.record),
                    "claim_receipt": {
                        "permit_reference": pending.claim_receipt.permit_reference,
                        "scope_key": pending.claim_receipt.scope_key,
                        "cancel_id": pending.claim_receipt.cancel_id,
                        "intent_fingerprint": pending.claim_receipt.intent_fingerprint,
                    },
                    "risk_claim": _claim_payload(facts.risk_claim),
                    "target_projection_id": pending.target_projection.projection_id,
                    "target_projection_sha256": pending.target_projection.projection_sha256,
                    "target_projection": facts.target.to_payload(),
                    "target_reservation": _reservation_payload(facts.reservation),
                    "submit_command_id": facts.submit_command.command_id,
                    "submit_authority_binding_sha256": facts.submit_command.authority_binding_sha256,
                    "submit_native_request_payload_sha256": facts.submit_command.native_request_payload_sha256,
                    "login_identity": {
                        "broker_id": facts.broker_id,
                        "user_id": facts.user_id,
                        "investor_id": facts.user_id,
                    },
                    "session_binding": dict(facts.session_payload),
                    "session_binding_sha256": facts.session_binding.session_binding_sha256,
                    "prepared_request_payload": dict(expected.request_payload),
                    "native_request_id": expected.native_request_id,
                    "local_queue_receipt_id": expected.local_queue_receipt_id,
                    "command_id": command.command_id,
                    "command_binding_sha256": command.authority_binding_sha256,
                }
            )

            final_monotonic_ns = time.monotonic_ns()
            authority_expires_at_ns = now_ns + min(
                pending.expires_monotonic_ns,
                facts.target.expires_at_ns,
                facts.query_provenance.expires_at_ns,
            ) - final_monotonic_ns
            final_wall_ns = time.time_ns()
            with self._lock:
                current = self._pending.get(permit_reference)
                if (
                    current is not pending
                    or pending.verified
                    or final_monotonic_ns >= pending.expires_monotonic_ns
                    or authority_expires_at_ns <= max(now_ns, final_wall_ns)
                ):
                    _fail("pending_authority_expired")
                self._pending[permit_reference] = replace(pending, verified=True)
            return CtpDispatchAuthority(
                authority_type=_AUTHORITY_TYPE,
                command_binding_sha256=command.authority_binding_sha256,
                approval_use_id=expected.approval_use_id,
                approval_digest=expected.approval_digest,
                source_digest_sha256=source_digest,
                verifier_id=_VERIFIER_ID,
                verified_at_ns=now_ns,
                expires_at_ns=authority_expires_at_ns,
            )
        finally:
            self._end_call()

    def _read_current_facts(
        self,
        intent: CancelIntent,
        receipt: CancelDispatchClaimReceiptV1,
        target_handle: CtpOrderTargetProjectionHandle,
    ) -> _CancelFacts:
        try:
            self._owner._assert_worker_thread()
            binding = self._owner._assert_active_owner()
            self._store.assert_writer_lease(self._scope, self._writer_lease)
        except (InvalidStateTransition, WriterLeaseUnavailable):
            _fail("owner_unavailable")
        if (
            type(binding) is not CtpCallbackSessionBindingV1
            or binding != self._owner._session_binding
            or binding.account_key != self._scope.account_key
            or binding.scope_key != self._scope.key
            or binding.trading_day != self._scope.trading_day
            or binding.session_generation_id != self._owner._session_generation_id
        ):
            _fail("owner_unavailable")

        record = self._store.get_cancel(intent.cancel_id, scope=self._scope)
        stored_intent = self._store.get_cancel_intent(intent.cancel_id, scope=self._scope)
        if type(record) is not CancelRecord or type(stored_intent) is not CancelIntent:
            _fail("cancel_record_unavailable")
        if (
            stored_intent != intent
            or stored_intent.fingerprint != intent.fingerprint
            or record.cancel_id != intent.cancel_id
            or record.scope_key != self._scope.key
            or record.target_intent_id != intent.target_intent_id
            or record.provider_order_id != intent.provider_order_id
            or record.payload_sha256 != intent.fingerprint
            or record.permit_reference != receipt.permit_reference
            or record.state is not ExecutionState.DISPATCHING
            or record.dispatch_attempts != 1
            or record.unknown_reason is not None
            or record.review_required is not False
        ):
            _fail("cancel_not_dispatching")

        risk_claim = self._verify_risk_claim(intent, receipt)
        try:
            target = self._store.read_ctp_order_target_projection(self._scope, target_handle)
        except ContractValidationError:
            _fail("target_unavailable")
        if type(target) is not CtpVerifiedOrderTargetProjection:
            _fail("target_unavailable")

        target_intent = self._store.get_intent(intent.target_intent_id, scope=self._scope)
        reservation = self._store.read_ctp_order_identity(self._scope, intent.target_intent_id)
        if type(target_intent) is not OrderIntent or type(reservation) is not CtpOrderIdentityReservation:
            _fail("submit_source_unavailable")
        command_id = stable_managed_command_id(
            "submit", intent.target_intent_id, reservation.runtime_order_id
        )
        submit_command = self._store.read_ctp_dispatch_command(self._scope, command_id)
        if type(submit_command) is not CtpDispatchCommand:
            _fail("submit_source_unavailable")

        login_facts = self._store.read_ctp_callback_session_context_facts(self._owner_handle)
        if (
            type(login_facts) is not tuple
            or len(login_facts) != 3
            or login_facts[0] != binding
            or type(login_facts[1]) is not str
            or not login_facts[1]
            or type(login_facts[2]) is not str
            or not login_facts[2]
        ):
            _fail("owner_unavailable")
        broker_id, user_id = login_facts[1], login_facts[2]
        session_payload = self._store._ctp_callback_session_binding_payload(binding)

        expected_order_id = self._decode_provider_order_id(intent.provider_order_id)
        provenance = target.query_provenance
        if type(provenance) is not CtpOrderTargetQueryProvenance:
            _fail("target_identity_mismatch")
        self._validate_target(
            intent,
            target_handle,
            target,
            provenance,
            target_intent,
            reservation,
            binding,
            session_payload,
            broker_id,
            user_id,
            expected_order_id,
        )
        self._validate_submit_source(
            target_intent,
            reservation,
            submit_command,
            binding,
            session_payload,
            broker_id,
            user_id,
            expected_order_id[0],
        )
        return _CancelFacts(
            record=record,
            cancel_intent=stored_intent,
            risk_claim=risk_claim,
            reservation=reservation,
            target_intent=target_intent,
            submit_command=submit_command,
            session_binding=binding,
            session_payload=MappingProxyType(dict(session_payload)),
            broker_id=broker_id,
            user_id=user_id,
            target=target,
            query_provenance=provenance,
        )

    def _verify_risk_claim(
        self, intent: CancelIntent, receipt: CancelDispatchClaimReceiptV1
    ) -> DispatchClaimBinding:
        try:
            risk_intent = self._admission_gate._risk_intent(intent)
            binding = self._admission_gate.verify_dispatch_claim_receipt(intent, receipt)
        except RiskExecutionCancelAdmissionError:
            _fail("risk_claim_unavailable")
        if (
            type(binding) is not DispatchClaimBinding
            or binding.permit_id != receipt.permit_reference
            or binding.scope != self._admission_gate._risk_scope
            or binding.intent_id != risk_intent.intent_id
            or binding.intent_hash != risk_intent.fingerprint
            or binding.cause_id != "dispatch-inflight:" + risk_intent.intent_id
        ):
            _fail("risk_claim_mismatch")
        return binding

    @staticmethod
    def _decode_provider_order_id(value: str) -> tuple[str, str]:
        from bt_api_py.ctp_service_execution import decode_ctp_provider_order_id

        try:
            return decode_ctp_provider_order_id(value)
        except ContractValidationError:
            _fail("invalid_cancel_identity")

    def _validate_target(
        self,
        cancel_intent: CancelIntent,
        handle: CtpOrderTargetProjectionHandle,
        target: CtpVerifiedOrderTargetProjection,
        provenance: CtpOrderTargetQueryProvenance,
        target_intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        binding: CtpCallbackSessionBindingV1,
        session_payload: Mapping[str, Any],
        broker_id: str,
        user_id: str,
        expected_order_id: tuple[str, str],
    ) -> None:
        now_monotonic_ns = time.monotonic_ns()
        reservation_payload = _reservation_payload(reservation)
        registration_digest = payload_sha256(
            {
                "registration_type": "ctp_order_identity_registration.v1",
                "reservation": reservation_payload,
                "session_binding": dict(session_payload),
            }
        )
        account_fingerprint = hashlib.sha256(
            f"{broker_id}:{user_id}".encode("ascii")
        ).hexdigest()
        expected_tags = (
            binding.source_instance_id,
            binding.native_client_epoch,
            binding.native_api_source_id,
            binding.native_spi_source_id,
            binding.native_api_generation,
            binding.source_connection_generation,
        )
        if (
            target.account_key != self._scope.account_key
            or target.scope_key != self._scope.key
            or target.trading_day != self._scope.trading_day
            or target.managed_intent_id != cancel_intent.target_intent_id
            or target.runtime_order_id != reservation.runtime_order_id
            or target.order_ref != reservation.order_ref
            or target.account_fingerprint_sha256 != account_fingerprint
            or target.registration_digest != registration_digest
            or target.instrument_id != target_intent.instrument
            or target.exchange_id != expected_order_id[0]
            or target.order_sys_id != expected_order_id[1]
            or target.session_generation_id != binding.session_generation_id
            or target.connection_generation != binding.connection_generation
            or target.query_front_id != binding.dispatch_front_id
            or target.query_session_id != binding.dispatch_session_id
            or target.query_complete is not True
            or target.query_terminal is not True
            or target.query_timed_out is not False
            or target.query_error_id not in (None, 0)
            or target.late_callback_count != 0
            or target.query_match_count != 1
            or target.provider_state not in {"OPEN", "PARTIAL"}
            or target.remaining_quantity <= 0
            or target.quantity != target.traded_quantity + target.remaining_quantity
            or target.front_id <= 0
            or target.session_id <= 0
            or target.verified_at_ns != provenance.issued_at_ns
            or target.expires_at_ns > provenance.expires_at_ns
            or now_monotonic_ns >= target.expires_at_ns
            or now_monotonic_ns >= provenance.expires_at_ns
            or provenance.owner_intent_id != self._owner_handle.owner_intent_id
            or provenance.account_key != self._scope.account_key
            or provenance.scope_key != self._scope.key
            or provenance.trading_day != self._scope.trading_day
            or provenance.managed_intent_id != cancel_intent.target_intent_id
            or provenance.runtime_order_id != reservation.runtime_order_id
            or provenance.order_ref != reservation.order_ref
            or provenance.writer_owner_id != self._writer_lease.owner_id
            or provenance.writer_fencing_token != self._writer_lease.fencing_token
            or provenance.session_binding_sha256 != binding.session_binding_sha256
            or provenance.session_generation_id != binding.session_generation_id
            or provenance.connection_generation != binding.connection_generation
            or provenance.dispatch_front_id != binding.dispatch_front_id
            or provenance.dispatch_session_id != binding.dispatch_session_id
            or provenance.source_tags != expected_tags
            or provenance.query_request_id != target.query_request_id
            or provenance.applied_source_sequence != provenance.end_source_sequence
            or provenance.end_source_sequence <= provenance.start_source_sequence
            or provenance.source_event_count != provenance.end_source_sequence - provenance.start_source_sequence
            or provenance.query_event_count != provenance.source_event_count
        ):
            _fail("target_identity_mismatch")
        if (
            handle.projection != target
            or not handle.projection_id
            or target_intent.scope != self._scope
            or reservation.account_key != self._scope.account_key
            or reservation.scope_key != self._scope.key
            or reservation.trading_day != self._scope.trading_day
            or reservation.managed_intent_id != cancel_intent.target_intent_id
            or cancel_intent.provider_order_id != self._encode_provider_order_id(*expected_order_id)
        ):
            _fail("target_identity_mismatch")

    @staticmethod
    def _encode_provider_order_id(exchange_id: str, order_sys_id: str) -> str:
        from bt_api_py.ctp_service_execution import encode_ctp_provider_order_id

        return encode_ctp_provider_order_id(exchange_id, order_sys_id)

    def _validate_submit_source(
        self,
        target_intent: OrderIntent,
        reservation: CtpOrderIdentityReservation,
        command: CtpDispatchCommand,
        binding: CtpCallbackSessionBindingV1,
        session_payload: Mapping[str, Any],
        broker_id: str,
        user_id: str,
        exchange_id: str,
    ) -> None:
        if (
            command.command_id
            != stable_managed_command_id(
                "submit", target_intent.intent_id, reservation.runtime_order_id
            )
            or command.operation != "SUBMIT"
            or command.account_key != self._scope.account_key
            or command.scope_key != self._scope.key
            or command.trading_day != self._scope.trading_day
            or command.reservation_managed_intent_id != target_intent.intent_id
            or command.order_ref != reservation.order_ref
            or command.status not in {"COMPLETED", "UNKNOWN"}
            or command.local_queue_receipt_queued is not True
            or type(command.local_queue_receipt_id) is not str
            or not command.local_queue_receipt_id
            or command.native_request_payload is None
            or command.native_request_payload_sha256 is None
            or command.session_binding_sha256 != binding.session_binding_sha256
            or dict(command.session_binding) != dict(session_payload)
            or command.request_payload_sha256 != payload_sha256(dict(command.request_payload))
            or command.native_request_payload_sha256
            != payload_sha256(dict(command.native_request_payload))
        ):
            _fail("submit_source_mismatch")
        try:
            submit_binding = CtpManagedDispatchBinding.from_command(command, reservation)
        except ContractValidationError:
            _fail("submit_source_mismatch")
        if (
            submit_binding.operation != "submit"
            or submit_binding.native_action_ref is not None
            or dict(submit_binding.request_payload) != dict(submit_binding.native_request_payload)
            or submit_binding.session_binding != dict(session_payload)
        ):
            _fail("submit_source_mismatch")
        expected_native: Mapping[str, Any]
        try:
            # Reuse the accepted SUBMIT translator so this cancel authority is
            # anchored to the same exact intent, login, instrument and exchange.
            from bt_api_py.ctp_order_target_query import _intent_native_payload

            expected_native = _intent_native_payload(
                target_intent,
                reservation,
                binding,
                broker_id,
                user_id,
                exchange_id,
            )
        except ContractValidationError:
            _fail("submit_source_mismatch")
        native_payload = dict(command.native_request_payload)
        if (
            native_payload != dict(expected_native)
            or native_payload.get("BrokerID") != broker_id
            or native_payload.get("UserID") != user_id
            or native_payload.get("InvestorID") != user_id
            or native_payload.get("InstrumentID") != target_intent.instrument
            or native_payload.get("ExchangeID") != exchange_id
            or native_payload.get("OrderRef") != reservation.order_ref
        ):
            _fail("submit_source_mismatch")

    def _build_prepared(
        self,
        intent: CancelIntent,
        receipt: CancelDispatchClaimReceiptV1,
        target_handle: CtpOrderTargetProjectionHandle,
        facts: _CancelFacts,
        *,
        native_request_id: int,
        local_queue_receipt_id: str,
    ) -> tuple[CtpManagedPreparedDispatch, str]:
        target = facts.target
        request_payload = {
            "BrokerID": facts.broker_id,
            "InvestorID": facts.user_id,
            "UserID": facts.user_id,
            "InstrumentID": facts.target_intent.instrument,
            "ExchangeID": target.exchange_id,
            "OrderRef": facts.reservation.order_ref,
            "OrderSysID": target.order_sys_id,
            "FrontID": target.front_id,
            "SessionID": target.session_id,
            "ActionFlag": "0",
            "LimitPrice": 0.0,
            "VolumeChange": 0,
        }
        command_id = stable_managed_command_id(
            "cancel",
            intent.target_intent_id,
            facts.reservation.runtime_order_id,
            intent.cancel_id,
        )
        approval_payload = {
            "approval_type": "ctp_risk_cancel_approval.v1",
            "cancel_intent": intent.to_payload(),
            "cancel_record": _cancel_record_payload(facts.record),
            "claim_receipt": {
                "permit_reference": receipt.permit_reference,
                "scope_key": receipt.scope_key,
                "cancel_id": receipt.cancel_id,
                "intent_fingerprint": receipt.intent_fingerprint,
            },
            "risk_claim": _claim_payload(facts.risk_claim),
            "target_projection_id": target_handle.projection_id,
            "target_projection_sha256": target_handle.projection_sha256,
            "target_projection": target.to_payload(),
            "reservation": _reservation_payload(facts.reservation),
            "submit_command_id": facts.submit_command.command_id,
            "submit_authority_binding_sha256": facts.submit_command.authority_binding_sha256,
            "submit_native_request_payload_sha256": facts.submit_command.native_request_payload_sha256,
            "login_identity": {
                "broker_id": facts.broker_id,
                "user_id": facts.user_id,
                "investor_id": facts.user_id,
            },
            "session_binding": dict(facts.session_payload),
            "session_binding_sha256": facts.session_binding.session_binding_sha256,
            "request_payload": request_payload,
            "native_request_id": native_request_id,
            "local_queue_receipt_id": local_queue_receipt_id,
            "command_id": command_id,
        }
        approval_digest = payload_sha256(approval_payload)
        prepared = CtpManagedPreparedDispatch(
            operation="cancel",
            command_id=command_id,
            managed_intent_id=intent.target_intent_id,
            runtime_order_id=facts.reservation.runtime_order_id,
            order_ref=facts.reservation.order_ref,
            request_payload=MappingProxyType(request_payload),
            order_ref_reservation=facts.reservation,
            approval_use_id=receipt.permit_reference,
            approval_digest=approval_digest,
            session_binding=MappingProxyType(dict(facts.session_payload)),
            session_generation_id=facts.session_binding.session_generation_id,
            dispatch_front_id=facts.session_binding.dispatch_front_id,
            dispatch_session_id=facts.session_binding.dispatch_session_id,
            native_request_id=native_request_id,
            local_queue_receipt_id=local_queue_receipt_id,
            managed_cancel_intent_id=intent.cancel_id,
            cancel_target_exchange_id=target.exchange_id,
            cancel_target_order_sys_id=target.order_sys_id,
            cancel_target_front_id=target.front_id,
            cancel_target_session_id=target.session_id,
        )
        return prepared, approval_digest

    def _validate_command(
        self,
        command: CtpDispatchCommand,
        prepared: CtpManagedPreparedDispatch,
        facts: _CancelFacts,
    ) -> None:
        correlation = command.correlation_key
        if (
            type(correlation) is not CtpDispatchCorrelationKey
            or correlation.version != 2
            or command.status != "READY"
            or command.operation != "CANCEL"
            or command.local_queue_receipt_queued is not True
            or command.account_key != self._scope.account_key
            or command.scope_key != self._scope.key
            or command.trading_day != self._scope.trading_day
            or command.command_id != prepared.command_id
            or command.reservation_managed_intent_id != prepared.managed_intent_id
            or command.order_ref is not None
            or command.cancel_target_order_ref != facts.reservation.order_ref
            or command.cancel_target_exchange_id != prepared.cancel_target_exchange_id
            or command.cancel_target_order_sys_id != prepared.cancel_target_order_sys_id
            or command.cancel_target_front_id != prepared.cancel_target_front_id
            or command.cancel_target_session_id != prepared.cancel_target_session_id
            or command.approval_use_id != prepared.approval_use_id
            or command.approval_digest != prepared.approval_digest
            or command.local_queue_receipt_id != prepared.local_queue_receipt_id
            or dict(command.request_payload) != dict(prepared.request_payload)
            or command.request_payload_sha256 != payload_sha256(dict(prepared.request_payload))
            or command.session_binding_sha256 != facts.session_binding.session_binding_sha256
            or dict(command.session_binding) != dict(facts.session_payload)
            or command.native_request_payload is None
            or command.native_request_payload_sha256 is None
        ):
            _fail("dispatch_command_mismatch")
        try:
            durable_binding = CtpManagedDispatchBinding.from_command(
                command, facts.reservation
            )
        except ContractValidationError:
            _fail("dispatch_command_mismatch")
        expected_native = dict(prepared.request_payload)
        action_ref = durable_binding.native_action_ref
        if type(action_ref) is not int or not 1 <= action_ref <= 2_147_483_647:
            _fail("dispatch_command_mismatch")
        expected_native["OrderActionRef"] = action_ref
        if (
            durable_binding.operation != "cancel"
            or durable_binding.managed_intent_id != prepared.managed_intent_id
            or durable_binding.managed_action_id != prepared.managed_cancel_intent_id
            or durable_binding.runtime_order_id != prepared.runtime_order_id
            or durable_binding.order_ref != prepared.order_ref
            or durable_binding.native_request_id != prepared.native_request_id
            or durable_binding.native_action_ref != action_ref
            or durable_binding.cancel_target_exchange_id != prepared.cancel_target_exchange_id
            or durable_binding.cancel_target_order_sys_id != prepared.cancel_target_order_sys_id
            or durable_binding.cancel_target_front_id != prepared.cancel_target_front_id
            or durable_binding.cancel_target_session_id != prepared.cancel_target_session_id
            or durable_binding.approval_use_id != prepared.approval_use_id
            or durable_binding.approval_digest != prepared.approval_digest
            or durable_binding.session_binding_sha256 != facts.session_binding.session_binding_sha256
            or durable_binding.session_generation_id != facts.session_binding.session_generation_id
            or durable_binding.dispatch_front_id != facts.session_binding.dispatch_front_id
            or durable_binding.dispatch_session_id != facts.session_binding.dispatch_session_id
            or durable_binding.local_queue_receipt_id != prepared.local_queue_receipt_id
            or dict(durable_binding.request_payload) != dict(prepared.request_payload)
            or dict(durable_binding.native_request_payload) != expected_native
            or command.native_request_payload_sha256 != payload_sha256(expected_native)
            or correlation.operation != "CANCEL"
            or correlation.command_id != prepared.command_id
            or correlation.request_payload_sha256 != command.request_payload_sha256
            or correlation.reservation_managed_intent_id != prepared.managed_intent_id
            or correlation.managed_action_id != prepared.managed_cancel_intent_id
            or correlation.runtime_order_id != prepared.runtime_order_id
            or correlation.order_ref != prepared.order_ref
            or correlation.cancel_target_exchange_id != prepared.cancel_target_exchange_id
            or correlation.cancel_target_order_sys_id != prepared.cancel_target_order_sys_id
            or correlation.cancel_target_front_id != prepared.cancel_target_front_id
            or correlation.cancel_target_session_id != prepared.cancel_target_session_id
            or correlation.approval_use_id != prepared.approval_use_id
            or correlation.approval_digest != prepared.approval_digest
            or correlation.session_binding_sha256 != facts.session_binding.session_binding_sha256
            or correlation.session_generation_id != facts.session_binding.session_generation_id
            or correlation.dispatch_front_id != facts.session_binding.dispatch_front_id
            or correlation.dispatch_session_id != facts.session_binding.dispatch_session_id
            or correlation.native_request_id != prepared.native_request_id
            or correlation.native_action_ref != action_ref
            or correlation.native_request_payload_sha256 != command.native_request_payload_sha256
        ):
            _fail("dispatch_command_mismatch")

    @staticmethod
    def _canonical_intent(intent: CancelIntent) -> str:
        from bt_api_execution import canonical_json

        return canonical_json(intent.to_payload())

    def _purge_expired_locked(self, now_monotonic_ns: int) -> None:
        expired = [
            key
            for key, value in self._pending.items()
            if value.expires_monotonic_ns <= now_monotonic_ns
        ]
        for key in expired:
            del self._pending[key]

    def _begin_call(self) -> None:
        if threading.current_thread() is not self._thread:
            _fail("wrong_thread")
        self._owner._assert_worker_thread()
        with self._lock:
            if self._busy:
                _fail("wrong_thread")
            self._busy = True

    def _end_call(self) -> None:
        with self._lock:
            self._busy = False


__all__ = ["CtpCancelAuthorityError", "CtpCancelAuthorityIssuerV1"]
