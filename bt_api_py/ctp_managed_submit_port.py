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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import current_thread
from time import monotonic, time_ns
from typing import Any, Callable

from bt_api_base.service_protocol import WireIdentity, WireScope
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
    OrderType,
    PositionEffect,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
    TimeInForce,
    WriterLease,
    WriterLeaseUnavailable,
    canonical_json,
)
from bt_api_execution.basic_admission import (
    RECOVERY_CLOSE_OPERATION,
    ExecutionAdmissionContextV1,
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


@dataclass(frozen=True, slots=True)
class _PreparedRecoveryClose:
    target_intent_id: str
    window_handle: object
    intent: OrderIntent
    principal_ref: str
    identity: WireIdentity
    wire_scope: WireScope

    @property
    def fingerprint(self) -> str:
        return self.intent.fingerprint


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
        recovery_target_intent_id_provider: Callable[[], str | None] | None = None,
        callback_pump_events: int = 32,
        callback_drain_limit: int = 4096,
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
        if type(callback_drain_limit) is not int or not 1 <= callback_drain_limit <= 100_000:
            raise ContractValidationError("callback drain bound must be from 1 to 100000")
        if recovery_target_intent_id_provider is not None and not callable(
            recovery_target_intent_id_provider
        ):
            raise ContractValidationError("trusted recovery review target provider is required")

        self._store = store
        self._scope = scope
        self._writer_lease = writer_lease
        self._trader = trader
        self._session_adapter = session_adapter
        self._reconciliation_port = reconciliation_port
        self._native_binding = native_binding
        self._session_generation_id = session_generation_id
        self._recovery_target_intent_id_provider = (
            recovery_target_intent_id_provider
        )
        self._callback_pump_events = callback_pump_events
        self._callback_drain_limit = callback_drain_limit
        self._thread = current_thread()
        self._binding_attempted = False
        self._risk_bridge_binding_attempted = False
        self._risk_bridge: Any | None = None
        self._prepared_recovery_sources: dict[str, _PreparedRecoveryClose] = {}
        self._gate: ExecutionBasicAdmissionGate | None = None
        self._issuer: CtpAdmissionAuthorityIssuerV1 | None = None
        self._owner: CtpExecutionOwner | None = None
        self._busy = False
        # The owner receives a queue receipt only after put_nowait succeeds and
        # this synchronous thread retrieves the exact enqueued envelope.
        self._queue: queue.Queue[_QueuedSubmit] = queue.Queue(maxsize=1)

    def authorize_position_baseline_submit(
        self,
        intent: OrderIntent,
        pending_record: ExecutionRecord,
        writer_lease: WriterLease,
    ) -> object:
        """Issue the Store's one-use baseline proof for an initial OPEN."""

        self._enter()
        try:
            _gate, _issuer, owner = self._require_bound()
            self._store.assert_writer_lease(self._scope, writer_lease)
            if writer_lease is not self._writer_lease or (
                type(intent) is not OrderIntent
                or intent.scope != self._scope
                or intent.position_effect.value != "OPEN"
                or type(pending_record) is not ExecutionRecord
                or pending_record.intent_id != intent.intent_id
                or pending_record.scope_key != self._scope.key
                or pending_record.payload_sha256 != intent.fingerprint
                or pending_record.state is not ExecutionState.PENDING_DISPATCH
                or pending_record.dispatch_attempts != 0
                or pending_record.review_required
                or self._store.get_intent(intent.intent_id, scope=self._scope) != intent
                or self._store.get(intent.intent_id, scope=self._scope) != pending_record
            ):
                raise ContractValidationError("exact first-dispatch OPEN row is required")
            self._get_or_reserve_order_identity(intent)
            window_handle = self._query_position_evidence_window(intent.intent_id, owner)
            authorize = getattr(self._store, "authorize_ctp_position_baseline_submit", None)
            if not callable(authorize):
                raise InvalidStateTransition("Store baseline submit authority is unavailable")
            authorization = authorize(
                self._scope,
                intent.intent_id,
                window_handle,
                owner_handle=owner._owner_handle,
                writer_lease=self._writer_lease,
            )
            if authorization is None:
                raise InvalidStateTransition("Store returned no baseline submit authorization")
            return authorization
        finally:
            self._leave()

    def prepare_recovery_close(
        self,
        *,
        target_intent_id: str,
        recovery_intent_id: str,
        principal_ref: str,
        identity: WireIdentity,
        wire_scope: WireScope,
    ) -> OrderIntent:
        """Build one same-day close from a fresh position delta and tracked OPEN.

        The requested intent id is only a correlation key. Every economic
        field comes from the durable parent and Store-issued source facts.
        This narrow implementation closes one same-day CTP contract; a
        different bucket, direction, or quantity is rejected before admission.
        An exact repeated child binding returns its durable non-pending result
        without issuing another position query or dispatch authorization.
        """

        self._enter()
        try:
            gate, _issuer, owner = self._require_bound()
            if (
                type(target_intent_id) is not str
                or not target_intent_id
                or target_intent_id != target_intent_id.strip()
                or type(recovery_intent_id) is not str
                or not recovery_intent_id
                or recovery_intent_id != recovery_intent_id.strip()
                or target_intent_id == recovery_intent_id
                or type(principal_ref) is not str
                or not principal_ref
                or type(identity) is not WireIdentity
                or identity.principal_ref != principal_ref
                or type(wire_scope) is not WireScope
                or wire_scope.strategy_ref != self._scope.strategy_id
                or not wire_scope.run_id
                or self._scope.trading_day is None
            ):
                raise ContractValidationError("invalid CTP recovery close target binding")
            owner._assert_worker_thread()
            owner._assert_active_owner()
            self._store.assert_writer_lease(self._scope, self._writer_lease)
            target_provider = self._recovery_target_intent_id_provider
            if not callable(target_provider):
                raise InvalidStateTransition(
                    "trusted CTP recovery review target is unavailable"
                )
            try:
                active_review_target = target_provider()
            except Exception:
                raise InvalidStateTransition(
                    "trusted CTP recovery review target is unavailable"
                ) from None
            if active_review_target != target_intent_id:
                raise InvalidStateTransition(
                    "CTP recovery target differs from the active review latch"
                )
            prepared = self._prepared_recovery_sources.get(recovery_intent_id)
            if prepared is not None:
                if (
                    prepared.target_intent_id != target_intent_id
                    or prepared.principal_ref != principal_ref
                    or prepared.identity != identity
                    or prepared.wire_scope != wire_scope
                ):
                    raise InvalidStateTransition(
                        "recovery intent id is already bound to another target or caller"
                    )
                stored_intent = self._store.get_intent(
                    recovery_intent_id, scope=self._scope
                )
                stored_record = self._store.get(
                    recovery_intent_id, scope=self._scope
                )
                if (
                    stored_intent != prepared.intent
                    or type(stored_record) is not ExecutionRecord
                    or stored_record.scope_key != self._scope.key
                    or stored_record.payload_sha256 != prepared.fingerprint
                    or stored_record.state
                    in {
                        ExecutionState.PENDING_ADMISSION,
                        ExecutionState.PENDING_DISPATCH,
                    }
                ):
                    raise InvalidStateTransition(
                        "recovery intent has no stable existing execution result"
                    )
                # The facade's durable intent admission returns this existing
                # non-dispatchable row before invoking any one-use authorizer.
                # Reusing the exact server-derived intent therefore provides
                # idempotent readback without querying positions or rebuilding
                # proof/allocation state.
                return prepared.intent
            stored_intent = self._store.get_intent(
                recovery_intent_id, scope=self._scope
            )
            stored_record = self._store.get(recovery_intent_id, scope=self._scope)
            if stored_intent is not None or stored_record is not None:
                expected_context = ExecutionAdmissionContextV1(
                    principal_ref=principal_ref,
                    identity=identity,
                    wire_scope=wire_scope,
                    execution_scope=self._scope,
                    action=RECOVERY_CLOSE_OPERATION,
                    recovery_target_intent_id=target_intent_id,
                )
                if (
                    type(stored_intent) is not OrderIntent
                    or stored_intent.scope != self._scope
                    or stored_intent.intent_id != recovery_intent_id
                    or stored_intent.position_effect
                    not in {
                        PositionEffect.CLOSE,
                        PositionEffect.CLOSE_TODAY,
                        PositionEffect.CLOSE_YESTERDAY,
                    }
                    or stored_intent.reduce_only is not True
                    or type(stored_record) is not ExecutionRecord
                    or stored_record.intent_id != recovery_intent_id
                    or stored_record.scope_key != self._scope.key
                    or stored_record.payload_sha256 != stored_intent.fingerprint
                    or stored_record.admission_context_sha256
                    != expected_context.fingerprint
                    or stored_record.dispatch_attempts < 1
                    or stored_record.state
                    in {
                        ExecutionState.PENDING_ADMISSION,
                        ExecutionState.PENDING_DISPATCH,
                    }
                ):
                    raise InvalidStateTransition(
                        "recovery intent id is bound to another or incomplete execution"
                    )
                # On a later call or process, the durable admission-context
                # digest proves this exact principal, scope, and parent binding.
                # Returning the stored intent makes the facade return its
                # existing non-dispatchable row without position queries or a
                # new one-use Risk/Store authorization.
                return stored_intent
            bridge = self._risk_bridge
            if bridge is None:
                raise InvalidStateTransition("CTP recovery Risk bridge is unavailable")
            attempted = getattr(bridge, "recovery_reservation_attempted", None)
            if not callable(attempted) or attempted(target_intent_id) is not False:
                raise InvalidStateTransition(
                    "CTP recovery target has already crossed its one-use reservation boundary"
                )
            parent = bridge.read_recovery_parent(
                target_intent_id=target_intent_id,
                principal_ref=principal_ref,
                wire_scope=wire_scope,
                writer_lease=self._writer_lease,
            )
            parent_intent, parent_record, parent_permit, _parent_risk_intent = parent
            if (
                type(parent_intent) is not OrderIntent
                or type(parent_record) is not ExecutionRecord
                or parent_intent.scope != self._scope
                or parent_intent.intent_id != target_intent_id
                or parent_record.intent_id != target_intent_id
                or parent_record.scope_key != self._scope.key
                or parent_record.payload_sha256 != parent_intent.fingerprint
                or parent_record.review_required is not True
                or parent_record.filled_quantity != Decimal("1")
                or parent_record.average_price is None
                or parent_record.cumulative_commission is not None
                or parent_record.provider_order_id is None
                or parent_intent.scope.trading_day != self._scope.trading_day
            ):
                raise InvalidStateTransition("tracked CTP parent is not a one-contract same-day fill")
            recovery_side = Side.SELL if parent_intent.side is Side.BUY else Side.BUY
            position_side = "LONG" if parent_intent.side is Side.BUY else "SHORT"
            child = OrderIntent(
                intent_id=recovery_intent_id,
                scope=self._scope,
                signal_id=recovery_intent_id,
                instrument=parent_intent.instrument,
                side=recovery_side,
                position_effect=PositionEffect.CLOSE_TODAY,
                order_type=OrderType.LIMIT,
                quantity=Decimal("1"),
                price=parent_record.average_price,
                time_in_force=TimeInForce.DAY,
                reduce_only=True,
                metadata_version=parent_intent.metadata_version,
                position_side=position_side,
                position_bucket="TODAY",
            )
            self._get_or_reserve_order_identity(child)
            window_handle = self._query_position_evidence_window(child.intent_id, owner)
            read_facts = getattr(
                self._store, "read_ctp_recovery_close_source_facts", None
            )
            if not callable(read_facts):
                raise InvalidStateTransition("durable CTP recovery source facts are unavailable")
            facts = read_facts(
                self._scope,
                target_intent_id,
                window_handle,
                owner_handle=owner._owner_handle,
                writer_lease=self._writer_lease,
                parent_permit=parent_permit,
            )
            from bt_api_execution.store import CtpRecoveryCloseSourceFacts

            if (
                type(facts) is not CtpRecoveryCloseSourceFacts
                or getattr(facts, "book_id", None) != self._scope.key
                or getattr(facts, "parent_intent_id", None) != target_intent_id
                or getattr(facts, "instrument", None) != child.instrument
                or getattr(facts, "strategy_id", None) != self._scope.strategy_id
                or getattr(facts, "parent_order_side", None) != parent_intent.side.value
                or getattr(facts, "recovery_order_side", None) != child.side.value
                or getattr(facts, "parent_position_side", None)
                != parent_permit.position_side
                or getattr(facts, "parent_position_bucket", None)
                != parent_permit.position_bucket
                or getattr(facts, "position_side", None) != child.position_side
                or getattr(facts, "position_bucket", None) != "TODAY"
                or getattr(facts, "parent_filled_quantity", None)
                != parent_record.filled_quantity
                or getattr(facts, "attributable_position_quantity", None)
                != child.quantity
                or getattr(facts, "session_binding_sha256", None)
                != owner._session_binding.session_binding_sha256
                or getattr(facts, "connection_generation", None)
                != owner._session_binding.connection_generation
                or getattr(facts, "trading_day", None) != self._scope.trading_day
                or getattr(facts, "valid_until_ns", 0) <= time_ns()
                or getattr(facts, "source_revision", 0) <= 0
            ):
                raise InvalidStateTransition("CTP recovery source does not match the one-lot close")
            self._prepared_recovery_sources[child.intent_id] = _PreparedRecoveryClose(
                target_intent_id,
                window_handle,
                child,
                principal_ref,
                identity,
                wire_scope,
            )
            return child
        finally:
            self._leave()

    def authorize_recovery_close_dispatch(
        self,
        intent: OrderIntent,
        target_intent_id: str,
        pending_record: ExecutionRecord,
        writer_lease: WriterLease,
    ) -> object:
        """Issue the exact Store auth for a fresh allocated recovery child."""

        self._enter()
        try:
            _gate, _issuer, owner = self._require_bound()
            if (
                writer_lease is not self._writer_lease
                or type(intent) is not OrderIntent
                or intent.scope != self._scope
                or not intent.reduce_only
                or intent.position_effect is not PositionEffect.CLOSE_TODAY
                or type(pending_record) is not ExecutionRecord
                or pending_record.intent_id != intent.intent_id
                or pending_record.scope_key != self._scope.key
                or pending_record.payload_sha256 != intent.fingerprint
                or pending_record.state is not ExecutionState.PENDING_DISPATCH
                or pending_record.dispatch_attempts != 0
                or pending_record.review_required
            ):
                raise ContractValidationError("exact first-dispatch CTP recovery child is required")
            prepared = self._prepared_recovery_sources.get(intent.intent_id)
            if (
                prepared is None
                or prepared.target_intent_id != target_intent_id
                or prepared.fingerprint != intent.fingerprint
            ):
                raise InvalidStateTransition("CTP recovery source window is missing or mismatched")
            bridge = self._risk_bridge
            if bridge is None:
                raise InvalidStateTransition("CTP recovery Risk bridge is unavailable")
            allocation, child_risk_intent = bridge.read_recovery_allocation(
                target_intent_id=target_intent_id,
                child_intent=intent,
                writer_lease=writer_lease,
            )
            gate = bridge._gate
            verify = getattr(gate, "verify_recovery_reduction_allocation", None)
            authorize = getattr(
                self._store, "authorize_ctp_recovery_close_dispatch", None
            )
            if not callable(verify) or not callable(authorize):
                raise InvalidStateTransition("CTP recovery close authorization is unavailable")
            authorization = authorize(
                self._scope,
                target_intent_id,
                intent.intent_id,
                prepared.window_handle,
                owner_handle=owner._owner_handle,
                writer_lease=writer_lease,
                recovery_allocation=allocation,
                recovery_allocation_verifier=verify,
                recovery_intent=child_risk_intent,
            )
            if authorization is None:
                raise InvalidStateTransition("Store returned no recovery close authorization")
            return authorization
        finally:
            self._leave()

    def recovery_position_window_for(
        self, intent: OrderIntent, target_intent_id: str
    ) -> object:
        """Return only the exact window cached by this prepare call."""

        self._assert_thread()
        if type(intent) is not OrderIntent or intent.scope != self._scope:
            raise ContractValidationError("same-scope CTP recovery intent is required")
        prepared = self._prepared_recovery_sources.get(intent.intent_id)
        if (
            prepared is None
            or prepared.target_intent_id != target_intent_id
            or prepared.fingerprint != intent.fingerprint
        ):
            raise InvalidStateTransition("CTP recovery position window is not bound to this child")
        return prepared.window_handle

    def _query_position_evidence_window(self, managed_intent_id: str, owner: CtpExecutionOwner) -> object:
        begin = getattr(self._store, "begin_ctp_position_evidence_window", None)
        finalize = getattr(self._store, "finalize_ctp_position_evidence_window", None)
        query = getattr(self._trader, "query_positions_result", None)
        session_scope = getattr(self._trader, "get_query_session_scope", None)
        if not all(callable(item) for item in (begin, finalize, query, session_scope)):
            raise InvalidStateTransition("trusted CTP position query window is unavailable")
        owner._assert_worker_thread()
        owner._assert_active_owner()
        self._store.assert_writer_lease(self._scope, self._writer_lease)
        fence = begin(
            self._scope,
            managed_intent_id,
            owner_handle=owner._owner_handle,
            writer_lease=self._writer_lease,
        )
        result = query(timeout=5.0)
        current_session_scope = session_scope()
        now_utc = datetime.now(timezone.utc)
        now_mono = monotonic()
        source = getattr(result, "query_source", None)
        completed_utc = getattr(source, "completed_at_utc", None)
        completed_mono = getattr(source, "completed_monotonic", None)
        expires_utc = (
            completed_utc + timedelta(seconds=5.0)
            if isinstance(completed_utc, datetime)
            else now_utc + timedelta(seconds=5.0)
        )
        expires_mono = (
            completed_mono + 5.0
            if type(completed_mono) in (int, float)
            else now_mono + 5.0
        )
        from bt_api_ctp.containers.ctp.ctp_position_evidence import (
            build_ctp_position_evidence,
        )

        evidence = build_ctp_position_evidence(
            result,
            session_state=current_session_scope,
            now_utc=now_utc,
            expires_at_utc=expires_utc,
            monotonic_now=now_mono,
            monotonic_expires_at=expires_mono,
        )
        drained = 0
        while drained < self._callback_drain_limit:
            batch = owner.pump_callbacks(
                min(self._callback_pump_events, 256, self._callback_drain_limit - drained)
            )
            if not batch:
                break
            drained += len(batch)
        if drained >= self._callback_drain_limit:
            raise InvalidStateTransition("CTP position callback drain exceeded its bound")
        self._store.assert_writer_lease(self._scope, self._writer_lease)
        return finalize(
            self._scope,
            fence,
            evidence,
            owner_handle=owner._owner_handle,
            writer_lease=self._writer_lease,
        )

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

    def bind_risk_dispatch_bridge(self, bridge: Any) -> None:
        """Bind the exact bridge to this port's already-created owner once."""

        self._assert_thread()
        if self._risk_bridge_binding_attempted:
            raise InvalidStateTransition("CTP submit port Risk binding is one-shot")
        self._risk_bridge_binding_attempted = True
        _gate, _issuer, owner = self._require_bound()
        owner.bind_risk_dispatch_bridge(bridge)
        self._risk_bridge = bridge

    @property
    def execution_owner(self) -> CtpExecutionOwner:
        """Return this port's exact owner after admission binding succeeds.

        Cancellation composition may borrow this owner, but cannot obtain an
        owner from an unbound port or cause a session/owner to be constructed.
        """

        self._assert_thread()
        _gate, _issuer, owner = self._require_bound()
        return owner

    def submit_authorized(
        self,
        intent: OrderIntent,
        authorization: ExecutionAdmissionAuthorizationV1,
    ) -> ProviderObservation:
        return self._submit_authorized(intent, authorization)

    def submit_with_position_baseline_authorized(
        self,
        intent: OrderIntent,
        authorization: ExecutionAdmissionAuthorizationV1,
        position_baseline_authorization: object,
    ) -> ProviderObservation:
        if position_baseline_authorization is None:
            raise ContractValidationError("Store-issued position baseline authorization is required")
        return self._submit_authorized(
            intent,
            authorization,
            position_baseline_authorization=position_baseline_authorization,
        )

    def submit_recovery_close_authorized(
        self,
        intent: OrderIntent,
        authorization: ExecutionAdmissionAuthorizationV1,
        recovery_close_authorization: object,
    ) -> ProviderObservation:
        if recovery_close_authorization is None:
            raise ContractValidationError("Store-issued recovery close authorization is required")
        return self._submit_authorized(
            intent,
            authorization,
            recovery_close_authorization=recovery_close_authorization,
        )

    def _submit_authorized(
        self,
        intent: OrderIntent,
        authorization: ExecutionAdmissionAuthorizationV1,
        *,
        position_baseline_authorization: object | None = None,
        recovery_close_authorization: object | None = None,
    ) -> ProviderObservation:
        """Dispatch one exact BasicAdmission claim and read callback evidence.

        Any missing or incomplete callback projection raises a typed
        not-ready error. The facade can then preserve the attempt as UNKNOWN;
        this port never retries or fabricates an acknowledgement.
        """

        self._enter()
        try:
            if (
                position_baseline_authorization is not None
                and recovery_close_authorization is not None
            ):
                raise ContractValidationError("CTP position dispatch authorizations are exclusive")
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
                dispatch = owner.dispatch_submit(
                    queued.prepared,
                    receipt,
                    position_baseline_authorization=position_baseline_authorization,
                    recovery_close_authorization=recovery_close_authorization,
                )
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
