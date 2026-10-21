"""Bind a retained BasicAdmission claim to one durable CTP SUBMIT command.

This module issues no permits and allocates no OrderRef, queue receipt, or
native RequestID. It converts one exact, already-claimed execution intent into
the CTP request stored by the managed outbox, then verifies that exact request
before the Store consumes the admission use. It never calls CTP or sends an
order. The Store's READY-to-CLAIMED transaction remains the one-use boundary.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from bt_api_execution import (
    CtpCallbackSessionBindingV1,
    CtpCallbackSessionOwnerHandle,
    CtpCallbackSessionStoreAdapter,
    CtpDispatchCorrelationKey,
    CtpDispatchAuthority,
    CtpDispatchCommand,
    CtpOrderIdentityReservation,
    ContractValidationError,
    ExecutionAdmissionAuthorizationV1,
    ExecutionBasicAdmissionGate,
    ExecutionBasicAdmissionDenied,
    ExecutionProviderOrderParametersV1,
    ExecutionScope,
    ExecutionState,
    InvalidStateTransition,
    OrderIntent,
    OrderType,
    PositionEffect,
    Side,
    SqliteExecutionStore,
    TimeInForce,
    WriterLease,
    canonical_json,
    payload_sha256,
)
from bt_api_execution.basic_admission import (
    RECOVERY_CLOSE_OPERATION,
    ExecutionAdmissionContextV1,
)
from bt_api_execution.ctp_single_worker_candidate import (
    CtpManagedPreparedDispatch,
    stable_managed_command_id,
)

_AUTHORITY_TYPE = "ctp_dispatch_authority.v1"
_VERIFIER_ID = "bt_api_py.ctp_admission_authority.v1"
_REFERENCE = re.compile(r"^[0-9a-f]{32}$", re.ASCII)
_DIRECTION = {Side.BUY: "0", Side.SELL: "1"}
_OFFSET = {
    (PositionEffect.OPEN, "ALL"): "0",
    (PositionEffect.CLOSE, "ALL"): "1",
    (PositionEffect.CLOSE, "TODAY"): "3",
    (PositionEffect.CLOSE, "YESTERDAY"): "4",
    (PositionEffect.CLOSE_TODAY, "TODAY"): "3",
    (PositionEffect.CLOSE_YESTERDAY, "YESTERDAY"): "4",
}


def _context_action_matches_order(
    context: ExecutionAdmissionContextV1, intent: OrderIntent
) -> bool:
    """Accept only a normal submit or an exact reduce-only recovery close."""

    if type(context) is not ExecutionAdmissionContextV1 or type(intent) is not OrderIntent:
        return False
    if context.action == "orders.submit":
        return context.recovery_target_intent_id is None
    target_intent_id = context.recovery_target_intent_id
    return (
        context.action == RECOVERY_CLOSE_OPERATION
        and type(target_intent_id) is str
        and bool(target_intent_id)
        and target_intent_id != intent.intent_id
        and intent.reduce_only is True
        and intent.position_effect
        in {
            PositionEffect.CLOSE,
            PositionEffect.CLOSE_TODAY,
            PositionEffect.CLOSE_YESTERDAY,
        }
    )


def _position_mode_matches_ctp_order(intent: OrderIntent) -> bool:
    """Map explicit position sides only for exact reducing close directions."""

    if type(intent) is not OrderIntent:
        return False
    if intent.position_side == "NET":
        return True
    if (
        intent.reduce_only is not True
        or intent.position_effect
        not in {
            PositionEffect.CLOSE,
            PositionEffect.CLOSE_TODAY,
            PositionEffect.CLOSE_YESTERDAY,
        }
    ):
        return False
    if intent.position_side == "LONG":
        return intent.side is Side.SELL
    if intent.position_side == "SHORT":
        return intent.side is Side.BUY
    return False


def _ctp_text(value: object, name: str, max_bytes: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isascii()
        or len(value.encode("ascii")) > max_bytes
        or any(ord(char) < 0x21 or ord(char) > 0x7E for char in value)
    ):
        raise ContractValidationError(f"invalid CTP {name}")
    return value


@dataclass(frozen=True, slots=True)
class CtpNativeDispatchBindingV1:
    """Composition-time account and identity mapping for one CTP instrument.

    ``investor_id`` is explicit: it is never inferred from the logged-in
    ``UserID``. This first adapter supports an identity instrument mapping only,
    because the current callback reconciler binds ``InstrumentID`` to the
    durable intent symbol. ``exchange_id`` is explicit code configuration.
    """

    account_ref: str
    scope_key: str
    investor_id: str
    intent_instrument: str
    provider_instrument: str
    exchange_id: str

    def __post_init__(self) -> None:
        _ctp_text(self.investor_id, "InvestorID", 12)
        _ctp_text(self.intent_instrument, "intent instrument", 80)
        _ctp_text(self.provider_instrument, "native InstrumentID", 80)
        _ctp_text(self.exchange_id, "ExchangeID", 8)
        if type(self.account_ref) is not str or not self.account_ref:
            raise ContractValidationError("CTP account_ref binding is required")
        if type(self.scope_key) is not str or not self.scope_key.startswith("scope:"):
            raise ContractValidationError("CTP scope_key binding is required")

    def to_payload(self) -> dict[str, str]:
        return {
            "binding_type": "ctp_native_dispatch_binding.v1",
            "account_ref": self.account_ref,
            "scope_key": self.scope_key,
            "investor_id": self.investor_id,
            "intent_instrument": self.intent_instrument,
            "provider_instrument": self.provider_instrument,
            "exchange_id": self.exchange_id,
        }


@dataclass(frozen=True, slots=True)
class _PendingSubmit:
    authorization: ExecutionAdmissionAuthorizationV1
    intent: OrderIntent
    prepared: CtpManagedPreparedDispatch | None
    expires_at_ns: int
    phase: str


class CtpAdmissionAuthorityIssuerV1:
    """Prepare and freshly verify one CTP SUBMIT under retained BasicAdmission.

    The execution facade must already have stored the exact BasicAdmission
    reference/context and claimed the intent into ``DISPATCHING``. The caller
    supplies request and local queue identifiers; this issuer does not allocate
    them. Existing OrderRef reservations and active callback sessions are read
    from the same SQLite Store.
    """

    def __init__(
        self,
        store: SqliteExecutionStore,
        scope: ExecutionScope,
        writer_lease: WriterLease,
        admission_gate: ExecutionBasicAdmissionGate,
        session_adapter: CtpCallbackSessionStoreAdapter,
        dispatch_port: object,
        native_binding: CtpNativeDispatchBindingV1,
        *,
        ttl_ns: int = 5_000_000_000,
        max_pending: int = 256,
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
            raise ContractValidationError("exact CTP writer lease is required")
        # CTP's single writer lease belongs to the account family. The Store
        # validates that lease against this strategy scope and its active owner.
        store.assert_writer_lease(scope, writer_lease)
        if type(admission_gate) is not ExecutionBasicAdmissionGate:
            raise ContractValidationError("exact BasicAdmission gate is required")
        if admission_gate._store is not store:
            raise ContractValidationError("BasicAdmission gate uses a different Store")
        if type(session_adapter) is not CtpCallbackSessionStoreAdapter:
            raise ContractValidationError("exact CTP callback session adapter is required")
        owner = session_adapter.owner_handle
        if (
            session_adapter.store is not store
            or session_adapter.scope != scope
            or session_adapter.writer_lease is not writer_lease
            or type(owner) is not CtpCallbackSessionOwnerHandle
            or owner.account_key != scope.account_key
            or owner.scope_key != scope.key
        ):
            raise ContractValidationError("CTP callback adapter differs from Store owner")
        if not callable(getattr(dispatch_port, "submit_authorized", None)):
            raise ContractValidationError("registered authorized CTP dispatch port is required")
        if type(native_binding) is not CtpNativeDispatchBindingV1:
            raise ContractValidationError("typed CTP native mapping is required")
        if (
            native_binding.account_ref != scope.account_ref
            or native_binding.scope_key != scope.key
        ):
            raise ContractValidationError("CTP native mapping differs from execution scope")
        if type(ttl_ns) is not int or not 1_000_000 <= ttl_ns <= 30_000_000_000:
            raise ContractValidationError("invalid CTP authority lifetime")
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ContractValidationError("invalid CTP authority capacity")

        self._store = store
        self._scope = scope
        self._writer_lease = writer_lease
        self._gate = admission_gate
        self._session_adapter = session_adapter
        self._owner_handle = owner
        self._dispatch_port = dispatch_port
        self._native_binding = native_binding
        self._ttl_ns = ttl_ns
        self._max_pending = max_pending
        self._lock = threading.RLock()
        self._pending: dict[str, _PendingSubmit] = {}

    def prepare_submit(
        self,
        intent: OrderIntent,
        authorization: ExecutionAdmissionAuthorizationV1,
        *,
        native_request_id: int,
        local_queue_receipt_id: str,
    ) -> CtpManagedPreparedDispatch:
        """Prepare one payload from exact Gate, Store, reservation and login facts."""

        if type(intent) is not OrderIntent:
            raise ContractValidationError("typed CTP execution intent is required")
        if type(authorization) is not ExecutionAdmissionAuthorizationV1:
            raise ContractValidationError("exact claimed BasicAdmission authorization is required")
        reference = authorization.permit_reference
        if type(reference) is not str or not _REFERENCE.fullmatch(reference):
            raise ContractValidationError("invalid BasicAdmission reference")
        now_ns = time.time_ns()
        if type(authorization.expires_at_ns) is not int or authorization.expires_at_ns <= now_ns:
            raise ContractValidationError("claimed BasicAdmission authorization is expired")
        self._verify_gate_claim(authorization, intent)
        self._retained_context(authorization)
        with self._lock:
            self._purge_expired_locked(now_ns)
            if reference in self._pending:
                raise ContractValidationError("BasicAdmission reference is already bound")
            if len(self._pending) >= self._max_pending:
                raise ContractValidationError("CTP authority capacity is full")
            pending = _PendingSubmit(
                authorization=authorization,
                intent=intent,
                prepared=None,
                expires_at_ns=authorization.expires_at_ns,
                phase="PREPARING",
            )
            self._pending[reference] = pending
        try:
            prepared, expires_at_ns = self._prepare_exact(
                intent,
                authorization,
                native_request_id=native_request_id,
                local_queue_receipt_id=local_queue_receipt_id,
                now_ns=now_ns,
            )
        except Exception:
            with self._lock:
                if self._pending.get(reference) is pending:
                    del self._pending[reference]
            raise
        ready = _PendingSubmit(
            authorization=authorization,
            intent=intent,
            prepared=prepared,
            expires_at_ns=expires_at_ns,
            phase="READY",
        )
        with self._lock:
            if self._pending.get(reference) is not pending:
                raise ContractValidationError("CTP authority preparation expired")
            final_now_ns = time.time_ns()
            if expires_at_ns <= max(now_ns, final_now_ns):
                del self._pending[reference]
                raise ContractValidationError("CTP authority expired during preparation")
            self._pending[reference] = ready
        return prepared

    def verify_action(self, command: CtpDispatchCommand, *, now_ns: int) -> CtpDispatchAuthority:
        """Bind one Store-provided queued SUBMIT to retained admission and source facts."""

        if type(command) is not CtpDispatchCommand:
            raise ContractValidationError("typed durable CTP command is required")
        if type(now_ns) is not int or now_ns <= 0:
            raise ContractValidationError("CTP authority verification time is invalid")
        if command.operation != "SUBMIT" or command.status != "READY":
            raise ContractValidationError("only a READY CTP SUBMIT can be authorized")
        if command.local_queue_receipt_queued is not True:
            raise ContractValidationError("persisted queued receipt is required")
        reference = command.approval_use_id
        with self._lock:
            pending = self._pending.get(reference)
        if pending is None or pending.phase != "READY" or pending.prepared is None:
            raise ContractValidationError("no live prepared BasicAdmission binding exists")
        if pending.expires_at_ns <= now_ns:
            raise ContractValidationError("prepared CTP authority is expired")

        intent = pending.intent
        authorization = pending.authorization
        self._verify_gate_claim(authorization, intent)
        context = self._retained_context(authorization)
        record, reservation, session, broker_id, user_id, session_payload = self._read_current_facts(
            intent, authorization, context
        )
        request_payload = self._native_request_payload(
            intent, authorization, reservation, broker_id, user_id
        )
        expected_prepared, _ = self._build_prepared(
            intent,
            authorization,
            reservation,
            session,
            session_payload,
            request_payload,
            pending.prepared.native_request_id,
            pending.prepared.local_queue_receipt_id,
            now_ns=now_ns,
        )
        if expected_prepared != pending.prepared:
            raise ContractValidationError("prepared CTP authority facts changed")

        durable = self._store.read_ctp_dispatch_command(self._scope, command.command_id)
        if type(durable) is not CtpDispatchCommand or durable != command:
            raise ContractValidationError("CTP verifier command differs from durable row")
        self._validate_command(command, expected_prepared, reservation, session, session_payload)
        if authorization.expires_at_ns <= now_ns:
            raise ContractValidationError("BasicAdmission authorization is expired")
        if intent.deadline_ns is not None and intent.deadline_ns <= now_ns:
            raise ContractValidationError("execution intent deadline has passed")
        self._store.assert_writer_lease(self._scope, self._writer_lease)

        source_payload = {
            "source_type": "ctp_basic_admission_authority_source.v1",
            "authorization": self._authorization_payload(authorization),
            "context": context.to_payload(),
            "intent": intent.to_payload(),
            "execution_record": {
                "intent_id": record.intent_id,
                "scope_key": record.scope_key,
                "payload_sha256": record.payload_sha256,
                "state": record.state.value,
                "dispatch_attempts": record.dispatch_attempts,
                "permit_reference": record.permit_reference,
                "basic_admission_reference": record.basic_admission_reference,
                "admission_context_sha256": record.admission_context_sha256,
            },
            "order_ref_reservation": self._reservation_payload(reservation),
            "native_mapping": self._native_binding.to_payload(),
            "login_identity": {
                "broker_id": broker_id,
                "user_id": user_id,
                "investor_id": self._native_binding.investor_id,
            },
            "session_binding": session_payload,
            "session_binding_sha256": session.session_binding_sha256,
            "request_payload": dict(expected_prepared.request_payload),
            "native_request_id": expected_prepared.native_request_id,
            "local_queue_receipt_id": expected_prepared.local_queue_receipt_id,
            "command_id": expected_prepared.command_id,
            "command_binding_sha256": command.authority_binding_sha256,
        }
        source_digest = payload_sha256(source_payload)
        final_now_ns = time.time_ns()
        with self._lock:
            authority_expires_at_ns = min(pending.expires_at_ns, now_ns + self._ttl_ns)
            if (
                self._pending.get(reference) is not pending
                or pending.phase != "READY"
                or pending.expires_at_ns <= max(now_ns, final_now_ns)
                or authority_expires_at_ns <= max(now_ns, final_now_ns)
            ):
                raise ContractValidationError("prepared CTP authority expired during verification")
            authority = CtpDispatchAuthority(
                authority_type=_AUTHORITY_TYPE,
                command_binding_sha256=command.authority_binding_sha256,
                approval_use_id=authorization.permit_reference,
                approval_digest=expected_prepared.approval_digest,
                source_digest_sha256=source_digest,
                verifier_id=_VERIFIER_ID,
                verified_at_ns=now_ns,
                expires_at_ns=authority_expires_at_ns,
            )
            return authority

    def _prepare_exact(
        self,
        intent: OrderIntent,
        authorization: ExecutionAdmissionAuthorizationV1,
        *,
        native_request_id: int,
        local_queue_receipt_id: str,
        now_ns: int,
    ) -> tuple[CtpManagedPreparedDispatch, int]:
        self._verify_gate_claim(authorization, intent)
        context = self._retained_context(authorization)
        record, reservation, session, broker_id, user_id, session_payload = self._read_current_facts(
            intent, authorization, context
        )
        request_payload = self._native_request_payload(
            intent, authorization, reservation, broker_id, user_id
        )
        return self._build_prepared(
            intent,
            authorization,
            reservation,
            session,
            session_payload,
            request_payload,
            native_request_id,
            local_queue_receipt_id,
            now_ns=now_ns,
        )

    def _read_current_facts(
        self,
        intent: OrderIntent,
        authorization: ExecutionAdmissionAuthorizationV1,
        context: ExecutionAdmissionContextV1,
    ) -> tuple[
        Any,
        CtpOrderIdentityReservation,
        CtpCallbackSessionBindingV1,
        str,
        str,
        dict[str, Any],
    ]:
        if intent.scope != self._scope or context.execution_scope != self._scope:
            raise ContractValidationError("CTP intent or Gate context differs from fixed scope")
        if not _context_action_matches_order(context, intent):
            raise ContractValidationError(
                "CTP admission action does not match the exact order intent"
            )
        self._store.assert_writer_lease(self._scope, self._writer_lease)
        record = self._store.get(intent.intent_id, scope=self._scope)
        stored_intent = self._store.get_intent(intent.intent_id, scope=self._scope)
        if (
            record is None
            or stored_intent is None
            or stored_intent != intent
            or record.payload_sha256 != intent.fingerprint
            or record.intent_id != intent.intent_id
            or record.scope_key != self._scope.key
            or record.state is not ExecutionState.DISPATCHING
            or record.dispatch_attempts != 1
            or record.review_required
            or record.basic_admission_reference != authorization.permit_reference
            or record.admission_context_sha256 != authorization.context_fingerprint
            or authorization.intent_fingerprint != intent.fingerprint
            or authorization.parameters.intent_fingerprint != intent.fingerprint
        ):
            raise ContractValidationError("durable CTP DISPATCHING claim differs from admission")
        reservation = self._store.read_ctp_order_identity(self._scope, intent.intent_id)
        if (
            type(reservation) is not CtpOrderIdentityReservation
            or reservation.account_key != self._scope.account_key
            or reservation.scope_key != self._scope.key
            or reservation.trading_day != self._scope.trading_day
            or reservation.managed_intent_id != intent.intent_id
        ):
            raise ContractValidationError("exact existing CTP OrderRef reservation is required")
        session, broker_id, user_id = self._store.read_ctp_callback_session_context_facts(
            self._owner_handle
        )
        if (
            type(session) is not CtpCallbackSessionBindingV1
            or session.owner_intent_id != self._owner_handle.owner_intent_id
            or session.account_key != self._scope.account_key
            or session.scope_key != self._scope.key
            or session.trading_day != self._scope.trading_day
        ):
            raise ContractValidationError("active CTP callback session differs from scope")
        _ctp_text(broker_id, "BrokerID", 10)
        _ctp_text(user_id, "UserID", 15)
        session_payload = self._store._ctp_callback_session_binding_payload(session)
        if payload_sha256(session_payload) != session.session_binding_sha256:
            raise ContractValidationError("active CTP session digest is inconsistent")
        return record, reservation, session, broker_id, user_id, session_payload

    def _retained_context(
        self, authorization: ExecutionAdmissionAuthorizationV1
    ) -> ExecutionAdmissionContextV1:
        """Read the context held by the exact Gate object, never caller input."""

        with self._gate._lock:
            claimed = self._gate._claimed_authorizations.get(authorization.permit_reference)
            if claimed is None or claimed.authorization is not authorization:
                raise ContractValidationError("exact retained BasicAdmission context is unavailable")
            context = claimed.held.context
        if (
            type(context) is not ExecutionAdmissionContextV1
            or payload_sha256(context.to_payload()) != authorization.context_fingerprint
        ):
            raise ContractValidationError("retained BasicAdmission context digest differs")
        return context

    def _verify_gate_claim(
        self,
        authorization: ExecutionAdmissionAuthorizationV1,
        intent: OrderIntent,
    ) -> None:
        try:
            self._gate.verify_claimed_authorization(
                authorization,
                intent,
                self._dispatch_port,
                self._writer_lease,
            )
        except ExecutionBasicAdmissionDenied as error:
            raise ContractValidationError(
                "exact live BasicAdmission claim is required for CTP dispatch"
            ) from error

    def _native_request_payload(
        self,
        intent: OrderIntent,
        authorization: ExecutionAdmissionAuthorizationV1,
        reservation: CtpOrderIdentityReservation,
        broker_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        parameters = authorization.parameters
        if (
            type(parameters) is not ExecutionProviderOrderParametersV1
            or type(reservation) is not CtpOrderIdentityReservation
            or reservation.managed_intent_id != intent.intent_id
            or intent.scope != self._scope
            or intent.instrument != self._native_binding.intent_instrument
            or self._native_binding.intent_instrument != self._native_binding.provider_instrument
            or parameters.provider_id != self._scope.provider
            or parameters.environment != self._scope.environment
            or parameters.provider_instrument != self._native_binding.provider_instrument
            or parameters.account_ref != self._scope.account_ref
            or parameters.strategy_id != self._scope.strategy_id
            or parameters.trading_day != self._scope.trading_day
            or parameters.intent_id != intent.intent_id
            or parameters.signal_id != intent.signal_id
            or parameters.intent_fingerprint != intent.fingerprint
            or parameters.side is not intent.side
            or parameters.position_effect is not intent.position_effect
            or parameters.order_type is not intent.order_type
            or parameters.quantity != intent.quantity
            or parameters.price != intent.price
            or parameters.time_in_force is not intent.time_in_force
            or parameters.reduce_only is not intent.reduce_only
            or parameters.metadata_version != intent.metadata_version
            or parameters.deadline_ns != intent.deadline_ns
            or dict(parameters.tags) != dict(intent.tags)
        ):
            raise ContractValidationError("BasicAdmission parameters differ from exact CTP intent")
        if intent.order_type is not OrderType.LIMIT or intent.price is None:
            raise ContractValidationError("CTP managed dispatch supports positive LIMIT orders only")
        if intent.time_in_force is not TimeInForce.DAY:
            raise ContractValidationError("CTP managed dispatch supports DAY/GFD only")
        if not _position_mode_matches_ctp_order(intent):
            raise ContractValidationError(
                "CTP explicit position side requires an opposite reduce-only close"
            )
        if intent.tags:
            raise ContractValidationError("CTP managed dispatch does not map intent tags to native fields")
        if intent.deadline_ns is not None and intent.deadline_ns <= time.time_ns():
            raise ContractValidationError("CTP intent dispatch deadline has passed")
        if self._native_binding.investor_id != user_id:
            raise ContractValidationError(
                "configured CTP InvestorID differs from authenticated login UserID"
            )
        expected_reduce_only = intent.position_effect is not PositionEffect.OPEN
        if intent.reduce_only is not expected_reduce_only:
            raise ContractValidationError("CTP effect and reduce-only intent disagree")
        offset = _OFFSET.get((intent.position_effect, intent.position_bucket))
        direction = _DIRECTION.get(intent.side)
        if (
            self._native_binding.exchange_id in {"SHFE", "INE"}
            and intent.position_effect is PositionEffect.CLOSE
            and intent.position_bucket == "ALL"
        ):
            raise ContractValidationError(
                "SHFE/INE CTP close requires an explicit TODAY or YESTERDAY bucket"
            )
        if offset is None or direction is None:
            raise ContractValidationError("unsupported CTP direction or position bucket")
        quantity = intent.quantity
        if (
            type(quantity) is not Decimal
            or quantity <= 0
            or quantity > Decimal(2_147_483_647)
            or quantity != Decimal(int(quantity))
        ):
            raise ContractValidationError("CTP order quantity must be positive integral lots")
        price = intent.price
        if type(price) is not Decimal or not price.is_finite() or price <= 0:
            raise ContractValidationError("CTP LIMIT price must be finite and positive")
        native_price = float(price)
        if not math.isfinite(native_price) or native_price <= 0 or Decimal(str(native_price)) != price:
            raise ContractValidationError("CTP LIMIT price is not exactly representable as a native double")
        return {
            "BrokerID": broker_id,
            "InvestorID": self._native_binding.investor_id,
            "UserID": user_id,
            "InstrumentID": self._native_binding.provider_instrument,
            "ExchangeID": self._native_binding.exchange_id,
            "OrderRef": reservation.order_ref,
            "Direction": direction,
            "CombOffsetFlag": offset,
            "CombHedgeFlag": "1",
            "VolumeTotalOriginal": int(quantity),
            "MinVolume": 1,
            "ForceCloseReason": "0",
            "IsAutoSuspend": 0,
            "UserForceClose": 0,
            "ContingentCondition": "1",
            "LimitPrice": native_price,
            "OrderPriceType": "2",
            "TimeCondition": "3",
            "VolumeCondition": "1",
        }

    def _build_prepared(
        self,
        intent: OrderIntent,
        authorization: ExecutionAdmissionAuthorizationV1,
        reservation: CtpOrderIdentityReservation,
        session: CtpCallbackSessionBindingV1,
        session_payload: dict[str, Any],
        request_payload: dict[str, Any],
        native_request_id: int,
        local_queue_receipt_id: str,
        *,
        now_ns: int,
    ) -> tuple[CtpManagedPreparedDispatch, int]:
        expires_at_ns = min(
            authorization.expires_at_ns,
            now_ns + self._ttl_ns,
            intent.deadline_ns if intent.deadline_ns is not None else authorization.expires_at_ns,
        )
        if expires_at_ns <= now_ns:
            raise ContractValidationError("CTP prepared authority expires before dispatch")
        command_id = stable_managed_command_id("submit", intent.intent_id, reservation.runtime_order_id)
        approval_payload = {
            "approval_type": "ctp_basic_admission_approval.v1",
            "authorization": self._authorization_payload(authorization),
            "context_fingerprint": authorization.context_fingerprint,
            "intent": intent.to_payload(),
            "native_mapping": self._native_binding.to_payload(),
            "order_ref_reservation": self._reservation_payload(reservation),
            "session_binding": session_payload,
            "session_binding_sha256": session.session_binding_sha256,
            "request_payload": request_payload,
            "native_request_id": native_request_id,
            "local_queue_receipt_id": local_queue_receipt_id,
            "command_id": command_id,
        }
        approval_digest = payload_sha256(approval_payload)
        prepared = CtpManagedPreparedDispatch(
            operation="submit",
            command_id=command_id,
            managed_intent_id=intent.intent_id,
            runtime_order_id=reservation.runtime_order_id,
            order_ref=reservation.order_ref,
            request_payload=MappingProxyType(dict(request_payload)),
            order_ref_reservation=reservation,
            approval_use_id=authorization.permit_reference,
            approval_digest=approval_digest,
            session_binding=MappingProxyType(dict(session_payload)),
            session_generation_id=session.session_generation_id,
            dispatch_front_id=session.dispatch_front_id,
            dispatch_session_id=session.dispatch_session_id,
            native_request_id=native_request_id,
            local_queue_receipt_id=local_queue_receipt_id,
        )
        return prepared, expires_at_ns

    def _validate_command(
        self,
        command: CtpDispatchCommand,
        prepared: CtpManagedPreparedDispatch,
        reservation: CtpOrderIdentityReservation,
        session: CtpCallbackSessionBindingV1,
        session_payload: dict[str, Any],
    ) -> None:
        correlation = command.correlation_key
        native_payload = command.native_request_payload
        if (
            type(correlation) is not CtpDispatchCorrelationKey
            or correlation.version != 2
            or command.status != "READY"
            or command.operation != "SUBMIT"
            or command.local_queue_receipt_queued is not True
            or command.account_key != self._scope.account_key
            or command.scope_key != self._scope.key
            or command.trading_day != self._scope.trading_day
            or command.command_id != prepared.command_id
            or command.reservation_managed_intent_id != prepared.managed_intent_id
            or command.order_ref != reservation.order_ref
            or command.cancel_target_order_ref is not None
            or command.cancel_target_exchange_id is not None
            or command.cancel_target_order_sys_id is not None
            or command.cancel_target_front_id is not None
            or command.cancel_target_session_id is not None
            or command.approval_use_id != prepared.approval_use_id
            or command.approval_digest != prepared.approval_digest
            or command.local_queue_receipt_id != prepared.local_queue_receipt_id
        ):
            raise ContractValidationError("durable CTP command differs from prepared SUBMIT")
        if (
            dict(command.request_payload) != dict(prepared.request_payload)
            or not isinstance(native_payload, Mapping)
            or native_payload != dict(prepared.request_payload)
            or command.request_payload_sha256 != payload_sha256(dict(prepared.request_payload))
            or command.native_request_payload_sha256 != payload_sha256(dict(prepared.request_payload))
            or dict(command.session_binding) != session_payload
            or command.session_binding_sha256 != session.session_binding_sha256
            or correlation.account_key != self._scope.account_key
            or correlation.scope_key != self._scope.key
            or correlation.trading_day != self._scope.trading_day
            or correlation.operation != "SUBMIT"
            or correlation.command_id != prepared.command_id
            or correlation.request_payload_sha256 != command.request_payload_sha256
            or correlation.reservation_managed_intent_id != prepared.managed_intent_id
            or correlation.managed_action_id != prepared.managed_intent_id
            or correlation.runtime_order_id != reservation.runtime_order_id
            or correlation.order_ref != reservation.order_ref
            or correlation.cancel_target_exchange_id is not None
            or correlation.cancel_target_order_sys_id is not None
            or correlation.cancel_target_front_id is not None
            or correlation.cancel_target_session_id is not None
            or correlation.approval_use_id != prepared.approval_use_id
            or correlation.approval_digest != prepared.approval_digest
            or correlation.session_binding_sha256 != session.session_binding_sha256
            or correlation.session_generation_id != session.session_generation_id
            or correlation.dispatch_front_id != session.dispatch_front_id
            or correlation.dispatch_session_id != session.dispatch_session_id
            or correlation.native_request_id != prepared.native_request_id
            or correlation.native_action_ref is not None
            or correlation.native_request_payload_sha256
            != command.native_request_payload_sha256
        ):
            raise ContractValidationError("durable CTP correlation differs from prepared SUBMIT")

    @staticmethod
    def _authorization_payload(
        authorization: ExecutionAdmissionAuthorizationV1,
    ) -> dict[str, Any]:
        parameters = authorization.parameters
        return {
            "authorization_type": "execution_admission_authorization.v1",
            "permit_reference": authorization.permit_reference,
            "context_fingerprint": authorization.context_fingerprint,
            "intent_fingerprint": authorization.intent_fingerprint,
            "validator_id": authorization.validator_id,
            "validator_version": authorization.validator_version,
            "capability_fingerprint": authorization.capability_fingerprint,
            "owner_id": authorization.owner_id,
            "fencing_token": authorization.fencing_token,
            "issued_at_ns": authorization.issued_at_ns,
            "expires_at_ns": authorization.expires_at_ns,
            "parameters": parameters.to_payload(),
        }

    @staticmethod
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

    def _purge_expired_locked(self, now_ns: int) -> None:
        expired = [
            reference
            for reference, pending in self._pending.items()
            if pending.expires_at_ns <= now_ns
        ]
        for reference in expired:
            del self._pending[reference]


__all__ = ["CtpAdmissionAuthorityIssuerV1", "CtpNativeDispatchBindingV1"]
