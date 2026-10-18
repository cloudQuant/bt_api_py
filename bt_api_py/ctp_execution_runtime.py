"""Thread-confined composition for one managed CTP execution owner.

This module is deliberately opt-in.  Constructing the provider creates no
Store, lease, account-family claim, TraderClient, or native object.  Those
resources are acquired only when ServiceHost invokes the provider on its
owner thread.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic_ns
from types import MappingProxyType
from typing import Any


_RECONCILIATION_OUTBOX_PAGE_SIZE = 64
_RECONCILIATION_BATCH_SIZE = 8
_RECONCILIATION_INTERVAL_NS = 1_000_000_000


class CtpExecutionRuntimeError(RuntimeError):
    """Finite, non-sensitive status for a failed CTP runtime operation."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CtpTraderConnectionConfig:
    """The explicit TD/MD front pair and credentials for one TraderClient."""

    front: str
    md_front: str
    broker_id: str
    user_id: str
    password: str = field(repr=False)
    ctp_env_profile: str = "config_front_pair"
    app_id: str = field(default="simnow_client_test", repr=False)
    auth_code: str = field(default="0000000000000000", repr=False)

    def __post_init__(self) -> None:
        for name in ("front", "md_front", "broker_id", "user_id", "password"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError(f"invalid CTP connection field: {name}")
        if (
            type(self.ctp_env_profile) is not str
            or not self.ctp_env_profile
            or self.ctp_env_profile != self.ctp_env_profile.strip()
        ):
            raise ValueError("invalid CTP environment profile")
        for name in ("app_id", "auth_code"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise ValueError(f"invalid CTP connection field: {name}")


@dataclass(frozen=True, slots=True)
class CtpRuntimeStatus:
    phase: str
    code: str | None
    ready: bool
    closed: bool


@dataclass(frozen=True, slots=True)
class CtpRuntimeAdmissionContext:
    """Exact owner-thread objects passed to trusted Risk composition code."""

    store: Any
    execution_scope: Any
    service_scope: Any
    writer_lease: Any
    trader: Any
    family_owner: Any
    callback_owner: Any
    session_adapter: Any
    session_generation_id: str
    reconciliation_port: Any
    submit_port: Any
    basic_admission_gate: Any
    execution_owner: Any
    native_binding: Any


@dataclass(frozen=True, slots=True)
class CtpRuntimeCredentialBindingSetupV1:
    """Exact startup-owned capability and installed ENTRY binding."""

    capability: object
    initial_binding: Any


@dataclass(frozen=True, slots=True)
class CtpRuntimeCancelAuthorizationRequestV1:
    """Value-only scope and target facts for a trusted cancel resolver."""

    cancel_intent: Any
    prepared_target: Any
    account_key: str
    scope_key: str
    trading_day: str
    session_generation_id: str
    callback_owner_intent_id: str
    account_fingerprint: str
    connection_generation: int
    environment_profile: str
    td_front: str
    md_front: str


@dataclass(frozen=True, slots=True)
class CtpExecutionRuntimeConfig:
    """Trusted inputs for one non-restartable CTP account-owner generation.

    ``prepare_trader`` is an application-owned composition hook.  It must use
    the TraderClient's real settlement confirmation/readback and established
    owner-sealed SimNow write-binding APIs.  This runtime never creates an
    approval, credential binding, Risk gate, or cutover proof.

    The basic gate factory is called after the real submit port exists and must
    register an ``ExecutionProviderValidatorV1`` bound to that exact port.  The
    Risk factory is called after the exact execution owner exists and returns
    the service admission resources described by this module's contract.
    """

    store_path: str | Path
    execution_scope: Any
    service_scope: Any
    lease_owner_id: str
    trader: CtpTraderConnectionConfig
    native_binding: Any
    prepare_trader: Callable[[Any], object]
    basic_admission_factory: Callable[[Any, Any, Any], Any]
    risk_admission_factory: Callable[[CtpRuntimeAdmissionContext], Mapping[str, Any]]
    cancel_recovery_binding_provider: Any | None = None
    startup_timeout_s: float = 15.0
    stop_timeout_s: float = 2.0
    lease_ttl_ns: int = 30_000_000_000
    callback_pump_events: int = 32
    drain_limit: int = 4096

    def __post_init__(self) -> None:
        if (
            type(self.store_path) is not str
            and not isinstance(self.store_path, Path)
        ) or not str(self.store_path).strip():
            raise ValueError("invalid CTP execution Store path")
        if type(self.lease_owner_id) is not str or not self.lease_owner_id.strip():
            raise ValueError("invalid CTP execution writer owner")
        if type(self.trader) is not CtpTraderConnectionConfig:
            raise ValueError("typed CTP TraderClient configuration is required")
        if self.trader.ctp_env_profile != "config_front_pair":
            raise ValueError("managed CTP runtime requires config_front_pair")
        for field_name in (
            "prepare_trader",
            "basic_admission_factory",
            "risk_admission_factory",
        ):
            if not callable(getattr(self, field_name)):
                raise ValueError(f"trusted CTP runtime hook is required: {field_name}")
        if self.cancel_recovery_binding_provider is not None and not callable(
            getattr(self.cancel_recovery_binding_provider, "resolve_verified_binding", None)
        ):
            raise ValueError("trusted cancel recovery binding provider is invalid")
        for field_name in ("startup_timeout_s", "stop_timeout_s"):
            value = getattr(self, field_name)
            if (
                type(value) not in (int, float)
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
                or value > 60
            ):
                raise ValueError(f"invalid CTP runtime timeout: {field_name}")
        if type(self.lease_ttl_ns) is not int or not 1_000_000 <= self.lease_ttl_ns <= 300_000_000_000:
            raise ValueError("invalid CTP execution lease duration")
        if type(self.callback_pump_events) is not int or not 1 <= self.callback_pump_events <= 256:
            raise ValueError("invalid CTP callback pump bound")
        if type(self.drain_limit) is not int or not 1 <= self.drain_limit <= 100_000:
            raise ValueError("invalid CTP callback drain bound")


class CtpExecutionRuntime:
    """One exact TraderClient, SQLite owner, and submit/cancel port generation."""

    def __init__(self, config: CtpExecutionRuntimeConfig, instance_id: str) -> None:
        self.config = config
        self.instance_id = instance_id
        self.owner_thread_id: int | None = None
        self._phase = "CREATED"
        self._code: str | None = None
        self._ready = False
        self._closed = False
        self._lease_healthy = False
        self._lease_expires_at_ns: int | None = None
        self._next_lease_renewal_ns: int | None = None
        self._lease_renewal_interval_ns = max(1, config.lease_ttl_ns // 3)
        self._next_reconciliation_ns: int | None = None
        self._reconciliation_journal_generation: str | None = None
        self._reconciliation_after_sequence = 0
        self._reconciliation_page_pending = False
        self._reconciliation_pending_intents: list[str] = []
        self._tracked_fill_review_intent_id: str | None = None
        self._tracked_fill_review_event_sequence: int | None = None
        self._store_closed = False
        self._native_start_attempted = False
        self._native_stop_attempted = False
        self._native_stop_complete = False
        self._last_stop_receipt: Any | None = None
        self._store: Any | None = None
        self._family_owner: Any | None = None
        self._writer_lease: Any | None = None
        self._callback_owner: Any | None = None
        self._session_adapter: Any | None = None
        self._trader: Any | None = None
        self._callback_verifier: Any | None = None
        self._reconciliation_port: Any | None = None
        self._submit_port: Any | None = None
        self._basic_gate: Any | None = None
        self._reconciliation_facade: Any | None = None
        self._execution_owner: Any | None = None
        self._cancel_gate: Any | None = None
        self._cancel_port: Any | None = None
        self._cancel_recovery_pending_intent_id: str | None = None
        self._cancel_recovery_pending_intent: Any | None = None
        self._cancel_recovery_binding: Any | None = None
        self._cancel_recovery_restore_failed = False
        self._order_gate: Any | None = None
        self._owned_risk_bridge: Any | None = None
        self._risk_bridge_closed = False
        self._cancel_quota_admission: Any | None = None
        self._credential_binding_setup: CtpRuntimeCredentialBindingSetupV1 | None = None
        self._resources: Any | None = None

    @property
    def status(self) -> CtpRuntimeStatus:
        return CtpRuntimeStatus(
            phase=self._phase,
            code=self._code,
            ready=self.is_ready(),
            closed=self._closed,
        )

    @property
    def last_stop_receipt(self) -> Any | None:
        return self._last_stop_receipt

    def _assert_owner_thread(self) -> None:
        if self.owner_thread_id is None or threading.get_ident() != self.owner_thread_id:
            raise CtpExecutionRuntimeError("OWNER_THREAD_REQUIRED")

    def is_ready(self) -> bool:
        if (
            not self._ready
            or not self._lease_healthy
            or self._closed
            or self._trader is None
            or self._lease_expires_at_ns is None
            or time.time_ns() >= self._lease_expires_at_ns
            or self._cancel_recovery_pending_intent_id is not None
            or self._cancel_recovery_restore_failed
        ):
            return False
        return self._trader_write_ready()

    def _trader_write_ready(self) -> bool:
        """Recheck the full managed-write gate, not only login readiness."""

        if self._trader is None:
            return False
        try:
            gate_state = self._trader.get_execution_gate_state()
            return (
                self._trader.is_trading_ready is True
                and isinstance(gate_state, Mapping)
                and gate_state.get("armed") is False
                and gate_state.get("managed") is True
                and gate_state.get("runtime_simnow_credential_binding_configured")
                is True
                and gate_state.get("runtime_simnow_write_verifier_configured")
                is True
            )
        except Exception:
            return False

    def _lease_failure(self) -> CtpExecutionRuntimeError:
        self._lease_healthy = False
        self._ready = False
        self._set_phase("LEASE_FAILED", "WRITER_LEASE_RENEWAL_FAILED")
        return CtpExecutionRuntimeError("WRITER_LEASE_RENEWAL_FAILED")

    def _renew_writer_lease_if_due(self, *, force: bool = False) -> bool:
        """Renew the existing fence without replacing its shared handle."""

        self._assert_owner_thread()
        lease = self._writer_lease
        store = self._store
        if (
            not self._lease_healthy
            or lease is None
            or store is None
            or self._family_owner is None
            or self._lease_expires_at_ns is None
            or self._next_lease_renewal_ns is None
        ):
            raise self._lease_failure()
        current_mono_ns = monotonic_ns()
        if time.time_ns() >= self._lease_expires_at_ns:
            raise self._lease_failure()
        if not force and current_mono_ns < self._next_lease_renewal_ns:
            return False

        try:
            from bt_api_execution import WriterLease

            renewed = store.acquire_or_renew_lease(
                self.config.execution_scope,
                self.config.lease_owner_id,
                ttl_ns=self.config.lease_ttl_ns,
                ctp_account_family_owner=self._family_owner,
            )
            if (
                type(renewed) is not WriterLease
                or renewed.scope_key != lease.scope_key
                or renewed.owner_id != lease.owner_id
                or renewed.fencing_token != lease.fencing_token
                or renewed.family_key != lease.family_key
                or renewed.family_owner_intent_id != lease.family_owner_intent_id
                or type(renewed.expires_at_ns) is not int
                or renewed.expires_at_ns <= time.time_ns()
            ):
                raise RuntimeError("writer lease identity changed")
            store.assert_writer_lease(self.config.execution_scope, lease)
        except Exception:
            raise self._lease_failure() from None

        # All ports and the callback owner retain `lease`; never substitute the
        # newly returned immutable snapshot. The store validates its owner and
        # fencing token against the renewed durable expiry.
        self._lease_expires_at_ns = renewed.expires_at_ns
        self._next_lease_renewal_ns = (
            monotonic_ns() + self._lease_renewal_interval_ns
        )
        return True

    def _startup_should_stop(self) -> bool:
        self._assert_owner_thread()
        if self._phase == "CLOSING":
            return True
        self._renew_writer_lease_if_due()
        return not self._lease_healthy

    def maintain(self, active_ids: frozenset[str]) -> None:
        """Renew and reconcile bounded callback-backed order state on owner thread."""

        self._assert_owner_thread()
        if type(active_ids) is not frozenset or any(
            type(item) is not str or not item for item in active_ids
        ):
            raise CtpExecutionRuntimeError("MAINTENANCE_INPUT_INVALID")
        if self._closed:
            return
        if not self._lease_healthy:
            raise CtpExecutionRuntimeError("WRITER_LEASE_RENEWAL_FAILED")
        self._renew_writer_lease_if_due()
        store = self._store
        lease = self._writer_lease
        if store is None or lease is None:
            raise self._lease_failure()
        try:
            store.assert_writer_lease(self.config.execution_scope, lease)
        except Exception:
            raise self._lease_failure() from None
        if self._cancel_recovery_pending_intent_id is not None:
            current_mono_ns = monotonic_ns()
            due_ns = self._next_reconciliation_ns
            if due_ns is not None and current_mono_ns < due_ns:
                return
            self._next_reconciliation_ns = (
                current_mono_ns + _RECONCILIATION_INTERVAL_NS
            )
            if (
                self._phase == "CANCEL_RECOVERY_PENDING"
                and not self._cancel_recovery_restore_failed
            ):
                self._reconcile_pending_cancel_recovery()
            return
        if self._tracked_fill_review_intent_id is not None:
            if self._phase != "TRACKED_FILL_REVIEW_PENDING":
                return
            current_mono_ns = monotonic_ns()
            due_ns = self._next_reconciliation_ns
            if due_ns is not None and current_mono_ns < due_ns:
                return
            self._next_reconciliation_ns = (
                current_mono_ns + _RECONCILIATION_INTERVAL_NS
            )
            try:
                owner = self._execution_owner
                if owner is None:
                    raise CtpExecutionRuntimeError(
                        "RECONCILIATION_SOURCE_UNAVAILABLE"
                    )
                owner.pump_callbacks(self.config.callback_pump_events)
                self._reconcile_tracked_fill_review()
            except CtpExecutionRuntimeError as error:
                self._ready = False
                self._set_phase("RECONCILIATION_FAILED", error.code)
                raise
            except Exception:
                self._ready = False
                self._set_phase(
                    "RECONCILIATION_FAILED", "EXECUTION_RECONCILIATION_FAILED"
                )
                raise CtpExecutionRuntimeError(
                    "EXECUTION_RECONCILIATION_FAILED"
                ) from None
            return
        if self._phase != "READY" or not self._ready:
            return
        current_mono_ns = monotonic_ns()
        due_ns = self._next_reconciliation_ns
        if (
            not self._reconciliation_page_pending
            and not self._reconciliation_pending_intents
            and due_ns is not None
            and current_mono_ns < due_ns
        ):
            return
        self._next_reconciliation_ns = (
            current_mono_ns + _RECONCILIATION_INTERVAL_NS
        )
        try:
            owner = self._execution_owner
            if owner is None:
                raise CtpExecutionRuntimeError(
                    "RECONCILIATION_SOURCE_UNAVAILABLE"
                )
            # Drain unsolicited settlement/audit ingress even when the
            # execution outbox has no active orders. Keep this bounded pump
            # after the exact writer-lease assertion above and before any
            # callback-backed order read.
            owner.pump_callbacks(self.config.callback_pump_events)
            if not self._reconciliation_pending_intents:
                self._refresh_reconciliation_candidates()
            self._reconcile_active_intents()
        except CtpExecutionRuntimeError as error:
            self._ready = False
            if (
                error.code == "EXECUTION_RECONCILIATION_REVIEW_REQUIRED"
                and self._tracked_fill_review_intent_id is not None
                and self._phase == "TRACKED_FILL_REVIEW_PENDING"
            ):
                raise
            self._set_phase("RECONCILIATION_FAILED", error.code)
            raise
        except Exception:
            self._ready = False
            self._set_phase("RECONCILIATION_FAILED", "EXECUTION_RECONCILIATION_FAILED")
            raise CtpExecutionRuntimeError("EXECUTION_RECONCILIATION_FAILED") from None

    def _reset_reconciliation_scan(self) -> None:
        self._reconciliation_after_sequence = 0
        self._reconciliation_page_pending = False
        self._reconciliation_pending_intents.clear()

    def _refresh_reconciliation_candidates(self) -> None:
        """Read one bounded outbox page and refresh only its exact-scope records."""

        from bt_api_execution import (
            ContractValidationError,
            ExecutionRecord,
            ExecutionState,
            OrderIntent,
        )
        from bt_api_execution.store import (
            ExecutionOutboxPageV1,
            ExecutionRecoveryIntentV1,
        )

        store = self._store
        scope = self.config.execution_scope
        if store is None or scope is None:
            raise CtpExecutionRuntimeError("RECONCILIATION_SOURCE_UNAVAILABLE")
        after_sequence = self._reconciliation_after_sequence
        try:
            page = store.read_execution_outbox_page(
                (scope,),
                after_sequence=after_sequence,
                limit=_RECONCILIATION_OUTBOX_PAGE_SIZE,
            )
        except ContractValidationError:
            # A replaced journal can have a lower high-water than the in-memory
            # cursor. Restart only this read-only scan from the beginning; the
            # Store still validates every row and generation at its own cut.
            if after_sequence == 0:
                raise
            self._reset_reconciliation_scan()
            page = store.read_execution_outbox_page(
                (scope,),
                after_sequence=0,
                limit=_RECONCILIATION_OUTBOX_PAGE_SIZE,
            )
        if type(page) is not ExecutionOutboxPageV1:
            raise CtpExecutionRuntimeError("RECONCILIATION_PAGE_INVALID")
        expected_generation = self._reconciliation_journal_generation
        if (
            expected_generation is not None
            and page.cut.journal_generation != expected_generation
        ):
            self._reset_reconciliation_scan()
            page = store.read_execution_outbox_page(
                (scope,),
                after_sequence=0,
                limit=_RECONCILIATION_OUTBOX_PAGE_SIZE,
            )
            if type(page) is not ExecutionOutboxPageV1:
                raise CtpExecutionRuntimeError("RECONCILIATION_PAGE_INVALID")
        self._reconciliation_journal_generation = page.cut.journal_generation
        active_states = frozenset(
            {
                ExecutionState.DISPATCHING,
                ExecutionState.UNKNOWN,
                ExecutionState.ACKED,
                ExecutionState.PARTIALLY_FILLED,
            }
        )
        pending_intents: list[str] = []
        for item in page.intents:
            if type(item) is not ExecutionRecoveryIntentV1:
                raise CtpExecutionRuntimeError("RECONCILIATION_PAGE_INVALID")
            record = item.record
            intent = item.intent
            if (
                type(record) is not ExecutionRecord
                or type(intent) is not OrderIntent
                or record.scope_key != scope.key
                or record.intent_id != intent.intent_id
                or intent.scope != scope
            ):
                raise CtpExecutionRuntimeError("RECONCILIATION_SCOPE_MISMATCH")
            if record.state in active_states:
                pending_intents.append(record.intent_id)
        self._reconciliation_pending_intents = pending_intents
        self._reconciliation_after_sequence = page.scanned_through
        self._reconciliation_page_pending = page.has_more
        if not page.has_more:
            # The next periodic sweep starts from the beginning so long-lived
            # active orders continue to be revisited without an unbounded cache.
            self._reconciliation_after_sequence = 0

    def _reconcile_active_intents(self) -> None:
        """Poll a finite round-robin batch through the existing callback-only port."""

        from bt_api_execution import (
            ExecutionRecord,
            ExecutionState,
            OrderIntent,
            ProviderObservation,
        )

        store = self._store
        scope = self.config.execution_scope
        facade = self._reconciliation_facade
        port = self._submit_port
        if store is None or facade is None or port is None:
            raise CtpExecutionRuntimeError("RECONCILIATION_SOURCE_UNAVAILABLE")
        if not self._reconciliation_pending_intents:
            return
        selected = tuple(
            self._reconciliation_pending_intents[:_RECONCILIATION_BATCH_SIZE]
        )
        del self._reconciliation_pending_intents[: len(selected)]
        active_states = frozenset(
            {
                ExecutionState.DISPATCHING,
                ExecutionState.UNKNOWN,
                ExecutionState.ACKED,
                ExecutionState.PARTIALLY_FILLED,
            }
        )
        for intent_id in selected:
            record = store.get(intent_id, scope=scope)
            if type(record) is not ExecutionRecord or record.scope_key != scope.key:
                raise CtpExecutionRuntimeError("RECONCILIATION_RECORD_UNAVAILABLE")
            if record.state not in active_states:
                continue
            intent = store.get_intent(intent_id, scope=scope)
            if type(intent) is not OrderIntent or intent.intent_id != intent_id:
                raise CtpExecutionRuntimeError("RECONCILIATION_INTENT_UNAVAILABLE")
            # CtpManagedSubmitPort.query reads the exact durable command and
            # callback snapshot on the owner thread. It never issues native
            # query/submit calls; None means there is no new trusted evidence.
            observation = port.query(intent)
            if observation is None:
                continue
            if (
                type(observation) is not ProviderObservation
                or observation.intent_id != intent_id
            ):
                raise CtpExecutionRuntimeError("RECONCILIATION_OBSERVATION_INVALID")
            updated = facade.record_provider_observation(observation)
            if type(updated) is not ExecutionRecord:
                raise CtpExecutionRuntimeError("RECONCILIATION_RECORD_INVALID")
            if updated.review_required:
                self._enter_tracked_fill_review(updated)
                raise CtpExecutionRuntimeError(
                    "EXECUTION_RECONCILIATION_REVIEW_REQUIRED"
                )

    def _read_tracked_fill_review_event(
        self,
        record: Any,
        *,
        expected_sequence: int | None = None,
    ) -> Any:
        """Accept only this bridge's exact durable ACKED_TRACKED review latch."""

        from bt_api_execution import ExecutionEvent, ExecutionRecord

        from bt_api_py.ctp_risk_dispatch_bridge import CtpRiskDispatchBridge

        bridge = self._owned_risk_bridge
        lease = self._writer_lease
        if (
            type(bridge) is not CtpRiskDispatchBridge
            or type(record) is not ExecutionRecord
            or lease is None
            or record.intent_id != self._tracked_fill_review_intent_id
            and self._tracked_fill_review_intent_id is not None
        ):
            raise CtpExecutionRuntimeError("TRACKED_FILL_REVIEW_CAPABILITY_INVALID")
        try:
            review_event = bridge.read_tracked_update_review(
                record, writer_lease=lease
            )
        except Exception:
            raise CtpExecutionRuntimeError(
                "TRACKED_FILL_REVIEW_CAPABILITY_INVALID"
            ) from None
        previous_sequence = self._tracked_fill_review_event_sequence
        if (
            type(review_event) is not ExecutionEvent
            or review_event.intent_id != record.intent_id
            or review_event.scope_key != record.scope_key
            or review_event.event_type != "manual_review_required"
            or (
                expected_sequence is not None
                and review_event.sequence != expected_sequence
            )
            or (
                previous_sequence is not None
                and review_event.sequence < previous_sequence
            )
        ):
            raise CtpExecutionRuntimeError("TRACKED_FILL_REVIEW_EVENT_INVALID")
        self._tracked_fill_review_intent_id = record.intent_id
        self._tracked_fill_review_event_sequence = review_event.sequence
        self._ready = False
        self._set_phase(
            "TRACKED_FILL_REVIEW_PENDING",
            "CTP_TRACKED_ORDER_UPDATE_REQUIRES_REVIEW",
        )
        return review_event

    def _enter_tracked_fill_review(self, record: Any) -> None:
        if self._tracked_fill_review_intent_id not in (None, record.intent_id):
            raise CtpExecutionRuntimeError("TRACKED_FILL_REVIEW_INTENT_MISMATCH")
        self._read_tracked_fill_review_event(record)

    def _reconcile_tracked_fill_review(self) -> None:
        """Maintain one tracked positive-fill order without dispatch or Risk release."""

        from bt_api_execution import (
            ExecutionRecord,
            ExecutionState,
            OrderIntent,
            ProviderObservation,
        )

        intent_id = self._tracked_fill_review_intent_id
        store = self._store
        scope = self.config.execution_scope
        facade = self._reconciliation_facade
        port = self._submit_port
        if intent_id is None or store is None or facade is None or port is None:
            raise CtpExecutionRuntimeError("TRACKED_FILL_REVIEW_SOURCE_UNAVAILABLE")
        record = store.get(intent_id, scope=scope)
        if (
            type(record) is not ExecutionRecord
            or record.scope_key != scope.key
            or record.intent_id != intent_id
            or record.review_required is not True
            or record.state
            not in {ExecutionState.PARTIALLY_FILLED, ExecutionState.FILLED}
            or record.filled_quantity <= 0
        ):
            raise CtpExecutionRuntimeError("TRACKED_FILL_REVIEW_RECORD_INVALID")
        self._read_tracked_fill_review_event(
            record, expected_sequence=self._tracked_fill_review_event_sequence
        )
        intent = store.get_intent(intent_id, scope=scope)
        if (
            type(intent) is not OrderIntent
            or intent.intent_id != intent_id
            or intent.scope != scope
            or record.payload_sha256 != intent.fingerprint
        ):
            raise CtpExecutionRuntimeError("TRACKED_FILL_REVIEW_INTENT_INVALID")

        observation = port.query(intent)
        if observation is None:
            return
        if (
            type(observation) is not ProviderObservation
            or observation.intent_id != intent_id
        ):
            raise CtpExecutionRuntimeError("TRACKED_FILL_REVIEW_OBSERVATION_INVALID")
        updated = facade.record_provider_observation(observation)
        if (
            type(updated) is not ExecutionRecord
            or updated.intent_id != intent_id
            or updated.scope_key != scope.key
            or updated.review_required is not True
            or updated.state
            not in {ExecutionState.PARTIALLY_FILLED, ExecutionState.FILLED}
            or updated.filled_quantity <= 0
        ):
            raise CtpExecutionRuntimeError("TRACKED_FILL_REVIEW_UPDATE_INVALID")
        self._read_tracked_fill_review_event(updated)

    def next_maintenance_delay(self) -> float | None:
        """Seconds until bounded owner-thread lease/order maintenance is due."""

        self._assert_owner_thread()
        pending_cancel_recovery = (
            self._cancel_recovery_pending_intent_id is not None
            or self._cancel_recovery_restore_failed
        )
        tracked_fill_review_pending = (
            self._tracked_fill_review_intent_id is not None
            and self._phase == "TRACKED_FILL_REVIEW_PENDING"
            and not self._ready
        )
        if (
            self._closed
            or (
                self._phase != "READY"
                and not pending_cancel_recovery
                and not tracked_fill_review_pending
            )
            or (
                not self._ready
                and not pending_cancel_recovery
                and not tracked_fill_review_pending
            )
            or not self._lease_healthy
            or self._next_lease_renewal_ns is None
        ):
            return None
        if (
            self._lease_expires_at_ns is not None
            and time.time_ns() >= self._lease_expires_at_ns
        ):
            return 0.0
        now_ns = monotonic_ns()
        next_due_ns = self._next_lease_renewal_ns
        if self._next_reconciliation_ns is not None:
            next_due_ns = min(next_due_ns, self._next_reconciliation_ns)
        return max(0, next_due_ns - now_ns) / 1_000_000_000

    def _validate_scope(self) -> None:
        from bt_api_base.service_protocol import WireScope
        from bt_api_execution import ExecutionScope

        from bt_api_py.ctp_admission_authority import CtpNativeDispatchBindingV1

        scope = self.config.execution_scope
        wire = self.config.service_scope
        binding = self.config.native_binding
        if (
            type(scope) is not ExecutionScope
            or scope.provider != "ctp"
            or scope.environment != "simulation"
            or not scope.trading_day
            or type(wire) is not WireScope
            or wire.run_id is not None
            or wire.account_refs
            or wire.manual_book_ref is not None
            or wire.group_id is not None
            or wire.leg_id is not None
            or wire.account_ref != scope.account_ref
            or wire.strategy_ref != scope.strategy_id
            or type(binding) is not CtpNativeDispatchBindingV1
            or binding.account_ref != scope.account_ref
            or binding.scope_key != scope.key
        ):
            raise CtpExecutionRuntimeError("TRUSTED_SCOPE_INVALID")

    def _set_phase(self, phase: str, code: str | None = None) -> None:
        self._phase = phase
        self._code = code

    def bootstrap(self) -> Any:
        """Create and return the owned component resources on the owner thread."""

        if self.owner_thread_id is not None:
            raise CtpExecutionRuntimeError("RUNTIME_ALREADY_STARTED")
        self.owner_thread_id = threading.get_ident()
        try:
            self._validate_scope()
            self._bootstrap_owned()
            self._renew_writer_lease_if_due(force=True)
            self._resources = self._make_resources(complete=True)
            self._next_reconciliation_ns = (
                monotonic_ns() + _RECONCILIATION_INTERVAL_NS
            )
            self._ready = True
            self._set_phase("READY")
            return self._resources
        except Exception as exc:
            # Keep only a finite code; never place a native exception, credential,
            # callback field, or operator-provided string in the public error.
            if self._code is None:
                self._code = (
                    exc.code
                    if type(exc) is CtpExecutionRuntimeError
                    else "BOOTSTRAP_FAILED"
                )
            self._ready = False
            self._set_phase("BOOTSTRAP_FAILED", self._code)
            if self._has_owned_state():
                resources = self._make_resources(complete=False)
                self._resources = resources
                from bt_api_py.service_components import ServiceResourceConstructionError

                raise ServiceResourceConstructionError(resources) from None
            if isinstance(exc, CtpExecutionRuntimeError):
                raise exc
            raise CtpExecutionRuntimeError(self._code) from None

    def _bootstrap_owned(self) -> None:
        from bt_api_ctp.ctp.callback_ingress import (
            CtpTraderCallbackIngressAckV2,
            CtpTraderCallbackIngressRecordV2,
        )
        from bt_api_ctp.ctp.client import TraderClient
        from bt_api_execution import SqliteExecutionStore
        from bt_api_execution.ctp_callback_session import CtpCallbackSessionStoreAdapter

        from bt_api_py.ctp_callback_evidence import CtpSessionIngressEvidenceVerifierV1
        from bt_api_py.ctp_managed_cancel_port import CtpManagedCancelPort
        from bt_api_py.ctp_managed_submit_port import CtpManagedSubmitPort
        from bt_api_py.ctp_service_execution import CtpOwnerCallbackReconciliationPort

        self._set_phase("STORE_OPEN")
        self._store = SqliteExecutionStore(self.config.store_path)
        self._set_phase("ACCOUNT_FAMILY_OWNER")
        self._family_owner = self._store.acquire_ctp_account_family_owner(
            self.config.execution_scope
        )
        self._set_phase("WRITER_LEASE")
        self._writer_lease = self._store.acquire_or_renew_lease(
            self.config.execution_scope,
            self.config.lease_owner_id,
            ttl_ns=self.config.lease_ttl_ns,
            ctp_account_family_owner=self._family_owner,
        )
        self._lease_expires_at_ns = self._writer_lease.expires_at_ns
        self._next_lease_renewal_ns = (
            monotonic_ns() + self._lease_renewal_interval_ns
        )
        self._lease_healthy = True
        self._set_phase("CALLBACK_OWNER")
        self._callback_owner = self._store.create_ctp_callback_session_owner(
            self.config.execution_scope,
            writer_lease=self._writer_lease,
            authorized_scopes=(self.config.execution_scope,),
        )
        self._session_adapter = CtpCallbackSessionStoreAdapter(
            self._store,
            self.config.execution_scope,
            self._callback_owner,
            self._writer_lease,
            CtpTraderCallbackIngressAckV2,
            CtpTraderCallbackIngressRecordV2,
        )

        self._set_phase("TRADER_CREATE")
        trader_config = self.config.trader
        self._trader = TraderClient(
            trader_config.front,
            trader_config.broker_id,
            trader_config.user_id,
            trader_config.password,
            app_id=trader_config.app_id,
            auth_code=trader_config.auth_code,
            auto_settlement_confirm=False,
            md_front=trader_config.md_front,
            ctp_env_profile=trader_config.ctp_env_profile,
        )
        self._trader.install_callback_ingress_sink(
            self._callback_owner,
            self._session_adapter.sink,
            self._session_adapter.bind_session,
            self._session_adapter.verify_native_call,
        )
        self._set_phase("TRADER_START")
        self._native_start_attempted = True
        self._trader.start(block=False)
        self._set_phase("LOGIN_WAIT")
        if not self._trader.wait_ready(
            self.config.startup_timeout_s,
            should_stop=self._startup_should_stop,
        ):
            raise self._fail("TRADER_LOGIN_TIMEOUT")

        facts = self._store.read_ctp_callback_session_context_facts(
            self._callback_owner
        )
        if type(facts) is not tuple or len(facts) != 3:
            raise self._fail("CALLBACK_SESSION_READBACK_FAILED")
        binding, broker_id, user_id = facts
        from bt_api_execution import CtpCallbackSessionBindingV1

        scope = self.config.execution_scope
        if (
            type(binding) is not CtpCallbackSessionBindingV1
            or binding.owner_intent_id != self._callback_owner.owner_intent_id
            or binding.account_key != scope.account_key
            or binding.scope_key != scope.key
            or binding.trading_day != scope.trading_day
            or not binding.session_generation_id
            or broker_id != trader_config.broker_id
            or user_id != trader_config.user_id
            or self.config.native_binding.investor_id != user_id
        ):
            raise self._fail("CALLBACK_SESSION_BINDING_MISMATCH")
        session_generation_id = binding.session_generation_id

        self._callback_verifier = CtpSessionIngressEvidenceVerifierV1(
            self._store,
            scope,
            self._writer_lease,
            self._session_adapter,
            self._trader,
            session_generation_id=session_generation_id,
        )
        self._reconciliation_port = CtpOwnerCallbackReconciliationPort(
            self._store,
            scope,
            self._writer_lease,
            self._session_adapter,
            session_generation_id=session_generation_id,
            callback_verifier=self._callback_verifier,
            trade_fact_verifier=self._callback_verifier,
        )

        self._set_phase("TRADER_PREPARE")
        try:
            self._renew_writer_lease_if_due(force=True)
            binding_setup = self.config.prepare_trader(self._trader)
            if self.config.cancel_recovery_binding_provider is not None:
                from bt_api_ctp.ctp.client import CtpRuntimeSimNowCredentialBinding

                if (
                    type(binding_setup) is not CtpRuntimeCredentialBindingSetupV1
                    or binding_setup.capability is None
                    or type(binding_setup.initial_binding)
                    is not CtpRuntimeSimNowCredentialBinding
                ):
                    raise ValueError("managed cancel credential setup is unavailable")
                self._credential_binding_setup = binding_setup
            self._renew_writer_lease_if_due(force=True)
        except Exception:
            if not self._lease_healthy:
                raise self._lease_failure() from None
            raise self._fail("TRADER_PREPARATION_FAILED") from None
        if not self._trader_write_ready():
            raise self._fail("TRADER_WRITE_READINESS_UNPROVEN")

        self._set_phase("SUBMIT_PORT")
        self._submit_port = CtpManagedSubmitPort(
            self._store,
            scope,
            self._writer_lease,
            self._trader,
            self._session_adapter,
            self._reconciliation_port,
            self.config.native_binding,
            session_generation_id=session_generation_id,
            callback_pump_events=self.config.callback_pump_events,
        )
        self._set_phase("BASIC_ADMISSION")
        self._basic_gate = self.config.basic_admission_factory(
            self._store, scope, self._submit_port
        )
        from bt_api_execution import ExecutionBasicAdmissionGate

        if type(self._basic_gate) is not ExecutionBasicAdmissionGate:
            raise self._fail("BASIC_ADMISSION_GATE_INVALID")
        self._submit_port.bind_basic_admission(self._basic_gate)
        self._execution_owner = self._submit_port.execution_owner

        context = CtpRuntimeAdmissionContext(
            store=self._store,
            execution_scope=scope,
            service_scope=self.config.service_scope,
            writer_lease=self._writer_lease,
            trader=self._trader,
            family_owner=self._family_owner,
            callback_owner=self._callback_owner,
            session_adapter=self._session_adapter,
            session_generation_id=session_generation_id,
            reconciliation_port=self._reconciliation_port,
            submit_port=self._submit_port,
            basic_admission_gate=self._basic_gate,
            execution_owner=self._execution_owner,
            native_binding=self.config.native_binding,
        )
        self._set_phase("RISK_ADMISSION")
        try:
            admission = self.config.risk_admission_factory(context)
        except Exception:
            raise self._fail("RISK_ADMISSION_FACTORY_FAILED") from None
        from bt_api_py.ctp_risk_dispatch_bridge import CtpRiskDispatchBridge

        if isinstance(admission, Mapping):
            candidate_order_gate = admission.get("order_gate")
            if type(candidate_order_gate) is CtpRiskDispatchBridge:
                # Retain its owned journal fence even if later composition
                # validation fails; close follows native stop and inbox drain.
                self._owned_risk_bridge = candidate_order_gate
        if not isinstance(admission, Mapping) or set(admission) - {
            "order_gate",
            "cancel_gate",
            "cancel_quota_admission",
        }:
            raise self._fail("RISK_ADMISSION_RESOURCES_INVALID")
        self._order_gate = admission.get("order_gate")
        self._cancel_gate = admission.get("cancel_gate")
        self._cancel_quota_admission = admission.get("cancel_quota_admission")
        if self._order_gate is None or self._cancel_gate is None:
            raise self._fail("RISK_ADMISSION_RESOURCES_INCOMPLETE")
        if self._cancel_quota_admission is not None and not callable(
            self._cancel_quota_admission
        ):
            raise self._fail("CANCEL_QUOTA_ADMISSION_INVALID")

        from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate

        if (
            type(self._cancel_gate) is not RiskExecutionCancelAdmissionGate
            or self._cancel_gate._store is not self._store
            or self._cancel_gate._execution_scope != scope
        ):
            raise self._fail("RISK_CANCEL_GATE_BINDING_INVALID")
        if type(self._order_gate) is not CtpRiskDispatchBridge:
            raise self._fail("RISK_DISPATCH_BRIDGE_REQUIRED")
        cancel_risk_gate = self._cancel_gate._gate
        if (
            self._order_gate.is_bound_to(cancel_risk_gate) is not True
            or self._order_gate._journal is not self._cancel_gate._journal
        ):
            raise self._fail("RISK_ORDER_CANCEL_AUTHORITY_MISMATCH")
        try:
            self._submit_port.bind_risk_dispatch_bridge(self._order_gate)
        except Exception:
            raise self._fail("RISK_DISPATCH_BRIDGE_BINDING_FAILED") from None
        from bt_api_execution import ManagedExecutionFacade

        self._reconciliation_facade = ManagedExecutionFacade(
            self._store,
            scope,
            writer_id=self._writer_lease.owner_id,
            admission_gate=self._order_gate,
            basic_admission_gate=self._basic_gate,
            borrowed_writer_lease=self._writer_lease,
        )
        self._cancel_port = CtpManagedCancelPort(
            self._execution_owner,
            self._cancel_gate,
            callback_pump_events=self.config.callback_pump_events,
            after_cancel_observation=self._after_cancel_observation,
        )

        # Preparation can emit settlement/query audit callbacks after login.
        # Apply them through this exact owner before publishing READY; the
        # Store's dispatch-time unapplied-ingress fence remains authoritative.
        self._set_phase("CALLBACK_READY_DRAIN")
        drained = 0
        try:
            while not self._callback_ingress_is_drained():
                remaining = self.config.drain_limit - drained
                if remaining <= 0:
                    raise self._fail("STARTUP_CALLBACK_DRAIN_LIMIT")
                self._renew_writer_lease_if_due()
                applied = self._execution_owner.pump_callbacks(
                    min(self.config.callback_pump_events, remaining)
                )
                if not applied:
                    raise self._fail("STARTUP_CALLBACK_DRAIN_INCOMPLETE")
                drained += len(applied)
        except CtpExecutionRuntimeError:
            raise
        except Exception:
            raise self._fail("STARTUP_CALLBACK_DRAIN_FAILED") from None

    def _fail(self, code: str) -> CtpExecutionRuntimeError:
        self._code = code
        return CtpExecutionRuntimeError(code)

    def _has_owned_state(self) -> bool:
        return any(
            value is not None
            for value in (
                self._store,
                self._trader,
                self._family_owner,
                self._writer_lease,
                self._callback_owner,
            )
        )

    def _callback_ingress_is_drained(self) -> bool:
        store = self._store
        owner = self._callback_owner
        lease = self._writer_lease
        if store is None or owner is None or lease is None:
            self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_STATUS_UNAVAILABLE")
            raise CtpExecutionRuntimeError("CALLBACK_DRAIN_STATUS_UNAVAILABLE")
        try:
            from bt_api_execution import CtpCallbackIngressDrainStatus

            status = store.read_ctp_callback_ingress_drain_status(
                owner,
                writer_lease=lease,
            )
        except Exception:
            self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_STATUS_UNAVAILABLE")
            raise CtpExecutionRuntimeError(
                "CALLBACK_DRAIN_STATUS_UNAVAILABLE"
            ) from None
        if type(status) is not CtpCallbackIngressDrainStatus:
            self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_STATUS_INVALID")
            raise CtpExecutionRuntimeError("CALLBACK_DRAIN_STATUS_INVALID")
        live_owner = status.owner_state in {"PREPARED", "ACTIVE"} and status.poison_code is None
        normal_stop_owner = (
            self._native_stop_complete is True
            and status.owner_state == "POISONED"
            and status.poison_code == "owner_stop"
        )
        if (
            status.owner_handle is not owner
            or not (live_owner or normal_stop_owner)
            or type(status.last_source_sequence) is not int
            or type(status.applied_source_sequence) is not int
            or type(status.pending_count) is not int
            or type(status.drained) is not bool
        ):
            self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_STATUS_INVALID")
            raise CtpExecutionRuntimeError("CALLBACK_DRAIN_STATUS_INVALID")
        return status.drained

    def _cancel_authorization_request(
        self, intent: Any, prepared_target: Any
    ) -> CtpRuntimeCancelAuthorizationRequestV1:
        from bt_api_execution import CtpCallbackSessionBindingV1

        store = self._store
        owner = self._callback_owner
        trader = self._trader
        execution_owner = self._execution_owner
        lease = self._writer_lease
        if (
            store is None
            or owner is None
            or trader is None
            or execution_owner is None
            or lease is None
        ):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_RUNTIME_UNAVAILABLE")

        scope = self.config.execution_scope
        store.assert_writer_lease(scope, lease)
        session_facts = store.read_ctp_callback_session_context_facts(owner)
        if type(session_facts) is not tuple or len(session_facts) != 3:
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_SESSION_UNAVAILABLE")
        binding, broker_id, user_id = session_facts
        if (
            type(binding) is not CtpCallbackSessionBindingV1
            or binding.owner_intent_id != owner.owner_intent_id
            or binding.account_key != scope.account_key
            or binding.scope_key != scope.key
            or binding.trading_day != scope.trading_day
            or binding.session_generation_id
            != execution_owner._session_generation_id
            or broker_id != self.config.trader.broker_id
            or user_id != self.config.trader.user_id
            or self.config.native_binding.investor_id != user_id
        ):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_SESSION_MISMATCH")

        state = trader.get_session_state()
        if not isinstance(state, Mapping):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_SESSION_UNAVAILABLE")
        account_fingerprint = state.get("account_fingerprint")
        trading_day = state.get("trading_day")
        connection_generation = state.get("connection_generation")
        if (
            type(account_fingerprint) is not str
            or not account_fingerprint
            or trading_day != scope.trading_day
            or type(connection_generation) is not int
            or connection_generation != binding.connection_generation
            or state.get("trading_ready") is not True
        ):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_SESSION_MISMATCH")

        from bt_api_py.ctp_managed_cancel_port import (
            CtpPreparedCancelAuthorizationFactsV1,
        )

        if (
            type(prepared_target) is not CtpPreparedCancelAuthorizationFactsV1
            or prepared_target.cancel_id != intent.cancel_id
            or prepared_target.target_intent_id != intent.target_intent_id
            or prepared_target.provider_order_id != intent.provider_order_id
            or prepared_target.account_key != scope.account_key
            or prepared_target.scope_key != scope.key
            or prepared_target.trading_day != scope.trading_day
            or prepared_target.session_generation_id != binding.session_generation_id
        ):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_TARGET_MISMATCH")

        return CtpRuntimeCancelAuthorizationRequestV1(
            cancel_intent=intent,
            prepared_target=prepared_target,
            account_key=scope.account_key,
            scope_key=scope.key,
            trading_day=scope.trading_day,
            session_generation_id=binding.session_generation_id,
            callback_owner_intent_id=owner.owner_intent_id,
            account_fingerprint=f"acct_{account_fingerprint}",
            connection_generation=connection_generation,
            environment_profile=self.config.trader.ctp_env_profile,
            td_front=self.config.trader.front,
            md_front=self.config.trader.md_front,
        )

    def _validate_cancel_recovery_binding(
        self,
        binding: Any,
        *,
        request: CtpRuntimeCancelAuthorizationRequestV1,
    ) -> None:
        from bt_api_ctp.ctp.client import CtpRuntimeSimNowCredentialBinding
        from bt_api_py._ctp_execution_authorization import (
            RECOVERY_APPROVAL_PURPOSE,
            SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
        )

        setup = self._credential_binding_setup
        if (
            type(setup) is not CtpRuntimeCredentialBindingSetupV1
            or type(binding) is not CtpRuntimeSimNowCredentialBinding
        ):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_BINDING_MISMATCH")
        initial = setup.initial_binding
        if (
            binding.owner is not initial.owner
            or binding.credential_binding_verifier
            is not initial.credential_binding_verifier
            or (binding.td_front, binding.md_front, binding.environment_profile)
            != (initial.td_front, initial.md_front, initial.environment_profile)
            or (binding.td_front, binding.md_front, binding.environment_profile)
            != (request.td_front, request.md_front, request.environment_profile)
            or not callable(binding.write_intent_verifier)
        ):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_BINDING_MISMATCH")

        initial_payload = initial.approval.payload
        payload = binding.approval.payload
        if not isinstance(initial_payload, Mapping) or not isinstance(payload, Mapping):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_BINDING_MISMATCH")
        mutable_fields = {
            "purpose",
            "schema_version",
            "recovery_actions",
            "approval_id",
            "nonce",
            "issued_at",
            "not_before",
            "expires_at",
            "revocation_snapshot_version",
            "authorized_instruments",
            "primary_instrument",
        }
        if any(
            payload.get(key) != value
            for key, value in initial_payload.items()
            if key not in mutable_fields
        ):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_SCOPE_MISMATCH")
        if (
            payload.get("purpose") != RECOVERY_APPROVAL_PURPOSE
            or payload.get("schema_version")
            != SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION
            or payload.get("account_fingerprint") != request.account_fingerprint
            or payload.get("trading_day") != request.trading_day
            or payload.get("connection_generation") != request.connection_generation
            or payload.get("environment_profile") != request.environment_profile
        ):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_SCOPE_MISMATCH")

        actions = payload.get("recovery_actions")
        if type(actions) is not list or len(actions) != 1 or not isinstance(actions[0], Mapping):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_ACTION_MISMATCH")
        action = actions[0]
        target = request.prepared_target
        expected = {
            "action_id": request.cancel_intent.cancel_id,
            "action_kind": "cancel",
            "instrument_id": target.instrument_id,
            "exchange_id": target.exchange_id,
            "account_fingerprint": request.account_fingerprint,
            "trading_day": request.trading_day,
            "connection_generation": request.connection_generation,
            "environment_profile": request.environment_profile,
        }
        if any(action.get(key) != value for key, value in expected.items()):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_ACTION_MISMATCH")

    def _prepare_cancel_recovery_binding(self, intent: Any) -> None:
        self._assert_owner_thread()
        if self._cancel_port is None or self._trader is None:
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_RUNTIME_UNAVAILABLE")
        self._renew_writer_lease_if_due(force=True)
        prepared = self._cancel_port.prepare_target(intent)
        provider = self.config.cancel_recovery_binding_provider
        if provider is None:
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_PROVIDER_UNAVAILABLE")
        if self._credential_binding_setup is None:
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_SETUP_UNAVAILABLE")

        request = self._cancel_authorization_request(intent, prepared)
        try:
            binding = provider.resolve_verified_binding(request)
        except Exception:
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_PROVIDER_FAILED") from None

        # Provider work may take time. Re-read the same persisted intent,
        # session and target through the existing preparation path before the
        # public client API installs the returned binding.
        self._renew_writer_lease_if_due(force=True)
        current = self._cancel_port.refresh_target(intent)
        if current != prepared or self._cancel_authorization_request(intent, current) != request:
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_CONTEXT_CHANGED")
        self._validate_cancel_recovery_binding(binding, request=request)
        # Recovery authority is operation-specific. Withdraw runtime readiness
        # before the client installs it; the facade may still complete this
        # already-authorized cancel on the owner thread, but no later request
        # can treat the recovery binding as normal entry authority.
        self._cancel_recovery_pending_intent_id = intent.cancel_id
        self._cancel_recovery_pending_intent = intent
        self._cancel_recovery_binding = binding
        self._cancel_recovery_restore_failed = False
        self._ready = False
        self._next_reconciliation_ns = monotonic_ns()
        self._set_phase("CANCEL_RECOVERY_PENDING")
        try:
            configured = self._trader.configure_runtime_simnow_credential_binding(
                self._credential_binding_setup.capability,
                binding,
            )
        except Exception:
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_BINDING_REJECTED") from None
        if (
            not isinstance(configured, Mapping)
            or configured.get("configured") is not True
            or configured.get("connection_generation") != request.connection_generation
        ):
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_BINDING_REJECTED")

    def _after_cancel_observation(
        self,
        intent: Any,
        observation: Any | None,
        dispatch_started: bool,
    ) -> None:
        """Restore startup ENTRY authority only after safe cancel evidence."""

        self._assert_owner_thread()
        from bt_api_execution import CancelIntent, CancelObservation, ExecutionState

        if type(intent) is not CancelIntent or type(dispatch_started) is not bool:
            self._ready = False
            self._cancel_recovery_restore_failed = True
            self._set_phase(
                "CANCEL_RECOVERY_RESTORE_FAILED", "CANCEL_RECOVERY_CONTEXT_MISMATCH"
            )
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_CONTEXT_MISMATCH")

        # Reconciliation also reports already-terminal durable cancels. When
        # no recovery binding is pending, that ordinary read-only replay must
        # not try to restore an ENTRY binding a second time.
        if self._cancel_recovery_pending_intent_id is None:
            if (
                self._cancel_recovery_restore_failed
                or self._cancel_recovery_binding is not None
            ):
                self._ready = False
                self._cancel_recovery_restore_failed = True
                self._set_phase(
                    "CANCEL_RECOVERY_RESTORE_FAILED", "CANCEL_RECOVERY_CONTEXT_MISMATCH"
                )
                raise CtpExecutionRuntimeError("CANCEL_RECOVERY_CONTEXT_MISMATCH")
            return

        if (
            self._cancel_recovery_pending_intent_id != intent.cancel_id
            or self._cancel_recovery_pending_intent != intent
            or self._cancel_recovery_binding is None
            or self._trader is None
            or self._credential_binding_setup is None
        ):
            self._ready = False
            self._cancel_recovery_restore_failed = True
            self._set_phase("CANCEL_RECOVERY_RESTORE_FAILED", "CANCEL_RECOVERY_CONTEXT_MISMATCH")
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_CONTEXT_MISMATCH")

        terminal_observation = (
            type(observation) is CancelObservation
            and observation.cancel_id == intent.cancel_id
            and observation.target_intent_id == intent.target_intent_id
            and observation.provider_order_id == intent.provider_order_id
            and observation.state in {ExecutionState.CANCELLED, ExecutionState.REJECTED}
        )
        known_not_dispatched = observation is None and dispatch_started is False
        if not terminal_observation and not known_not_dispatched:
            self._ready = False
            self._set_phase("CANCEL_RECOVERY_PENDING", "CANCEL_OUTCOME_UNRESOLVED")
            return

        try:
            self._renew_writer_lease_if_due(force=True)
            configured = self._trader.configure_runtime_simnow_credential_binding(
                self._credential_binding_setup.capability,
                self._credential_binding_setup.initial_binding,
            )
            current_session = self._trader.get_session_state()
            if (
                not isinstance(configured, Mapping)
                or configured.get("configured") is not True
                or not isinstance(current_session, Mapping)
                or type(current_session.get("connection_generation")) is not int
                or configured.get("connection_generation")
                != current_session.get("connection_generation")
                or not self._trader_write_ready()
            ):
                raise CtpExecutionRuntimeError("CANCEL_RECOVERY_RESTORE_REJECTED")
        except Exception:
            self._ready = False
            self._cancel_recovery_restore_failed = True
            self._set_phase(
                "CANCEL_RECOVERY_RESTORE_FAILED", "CANCEL_RECOVERY_RESTORE_REJECTED"
            )
            raise CtpExecutionRuntimeError("CANCEL_RECOVERY_RESTORE_REJECTED") from None

        self._cancel_recovery_pending_intent_id = None
        self._cancel_recovery_pending_intent = None
        self._cancel_recovery_binding = None
        self._cancel_recovery_restore_failed = False
        self._ready = True
        self._set_phase("READY")

    def _reconcile_pending_cancel_recovery(self) -> None:
        """Read and apply one bounded terminal callback for the pending cancel."""

        self._assert_owner_thread()
        from bt_api_execution import (
            CancelIntent,
            CancelObservation,
            CancelRecord,
            ExecutionState,
            ManagedCancellationFacade,
        )

        intent = self._cancel_recovery_pending_intent
        store = self._store
        lease = self._writer_lease
        scope = self.config.execution_scope
        port = self._cancel_port
        gate = self._cancel_gate
        if (
            type(intent) is not CancelIntent
            or intent.cancel_id != self._cancel_recovery_pending_intent_id
            or intent.scope != scope
            or store is None
            or lease is None
            or port is None
            or gate is None
        ):
            self._ready = False
            self._cancel_recovery_restore_failed = True
            self._set_phase(
                "CANCEL_RECOVERY_RESTORE_FAILED", "CANCEL_RECOVERY_CONTEXT_MISMATCH"
            )
            return

        try:
            stored_intent = store.get_cancel_intent(intent.cancel_id, scope=scope)
            record = store.get_cancel(intent.cancel_id, scope=scope)
        except Exception:
            self._set_phase(
                "CANCEL_RECOVERY_PENDING", "CANCEL_RECOVERY_READBACK_UNAVAILABLE"
            )
            return
        normal_pending_record = (
            type(record) is CancelRecord
            and record.state
            in {ExecutionState.DISPATCHING, ExecutionState.ACKED}
            and record.unknown_reason is None
            and record.review_required is False
        )
        unresolved_dispatch_record = (
            type(record) is CancelRecord
            and record.state is ExecutionState.UNKNOWN
            and record.unknown_reason == "cancel_outcome_unknown"
            and record.review_required is True
        )
        if (
            type(stored_intent) is not CancelIntent
            or stored_intent != intent
            or type(record) is not CancelRecord
            or record.cancel_id != intent.cancel_id
            or record.scope_key != scope.key
            or record.target_intent_id != intent.target_intent_id
            or record.provider_order_id != intent.provider_order_id
            or record.payload_sha256 != intent.fingerprint
            or record.dispatch_attempts != 1
            or not (normal_pending_record or unresolved_dispatch_record)
        ):
            self._ready = False
            self._cancel_recovery_restore_failed = True
            self._set_phase(
                "CANCEL_RECOVERY_RESTORE_FAILED", "CANCEL_RECOVERY_RECORD_MISMATCH"
            )
            return

        try:
            # This exact port method pumps/reconciles existing callback and
            # Store state only; it cannot issue another native cancel.
            observation = port.reconcile_cancel(intent)
        except Exception:
            self._set_phase(
                "CANCEL_RECOVERY_PENDING", "CANCEL_RECOVERY_READBACK_UNAVAILABLE"
            )
            return
        if (
            type(observation) is not CancelObservation
            or observation.cancel_id != intent.cancel_id
            or observation.target_intent_id != intent.target_intent_id
            or observation.provider_order_id != intent.provider_order_id
            or observation.state
            not in {ExecutionState.CANCELLED, ExecutionState.REJECTED}
        ):
            self._set_phase("CANCEL_RECOVERY_PENDING", "CANCEL_OUTCOME_UNRESOLVED")
            return

        try:
            facade = ManagedCancellationFacade(
                store,
                scope,
                acquire_writer_lease=lambda: lease,
                admission_gate=gate,
                allow_unprotected=False,
            )
            reconciled = facade.reconcile(observation)
        except Exception:
            self._ready = False
            self._cancel_recovery_restore_failed = True
            self._set_phase(
                "CANCEL_RECOVERY_RESTORE_FAILED",
                "CANCEL_RISK_RECONCILIATION_FAILED",
            )
            return
        if (
            type(reconciled) is not CancelRecord
            or reconciled.cancel_id != intent.cancel_id
            or reconciled.scope_key != scope.key
            or reconciled.target_intent_id != intent.target_intent_id
            or reconciled.provider_order_id != intent.provider_order_id
            or reconciled.state is not observation.state
            or reconciled.dispatch_attempts != 1
            or reconciled.review_required is not False
            or self._cancel_recovery_pending_intent_id is not None
            or self._cancel_recovery_pending_intent is not None
            or self._cancel_recovery_restore_failed
        ):
            self._ready = False
            self._cancel_recovery_restore_failed = True
            self._set_phase(
                "CANCEL_RECOVERY_RESTORE_FAILED",
                "CANCEL_RISK_RECONCILIATION_FAILED",
            )

    def _make_resources(self, *, complete: bool) -> Any:
        from bt_api_py.service_components import ComponentResources

        values: dict[str, object] = {}
        if complete:
            if any(
                value is None
                for value in (
                    self._store,
                    self._submit_port,
                    self._basic_gate,
                    self._order_gate,
                    self._cancel_gate,
                    self._cancel_port,
                )
            ):
                raise CtpExecutionRuntimeError("RUNTIME_RESOURCES_INCOMPLETE")
            admission: dict[str, object] = {
                "order_gate": self._order_gate,
                "cancel_gate": self._cancel_gate,
                "basic_admission_gate": self._basic_gate,
            }
            if self._cancel_quota_admission is not None:
                admission["cancel_quota_admission"] = self._cancel_quota_admission
            values = {
                "store": self._store,
                "borrowed_writer_lease": self._writer_lease,
                "targets": MappingProxyType(
                    {self.config.service_scope: self.config.execution_scope}
                ),
                "dispatch": self._submit_port,
                "admission": MappingProxyType(admission),
                "cancel_dispatch": self._cancel_port,
                "cancel_before_dispatch": self._prepare_cancel_recovery_binding,
            }
        return ComponentResources.owned(
            values,
            ready=self.is_ready,
            maintain=self.maintain,
            next_maintenance_delay=self.next_maintenance_delay,
            close=self.close,
            internal_ports={},
        )

    def close(self) -> None:
        """Pump available callbacks, confirm native stop, then close the Store."""

        self._assert_owner_thread()
        if self._closed:
            return
        self._ready = False
        self._set_phase("CLOSING")
        callback_drain_failed = False
        if (
            self._store is not None
            and self._callback_owner is not None
            and self._execution_owner is not None
            and not self._native_stop_attempted
        ):
            drained = 0
            try:
                while drained < self.config.drain_limit:
                    remaining = self.config.drain_limit - drained
                    results = self._execution_owner.pump_callbacks(
                        min(self.config.callback_pump_events, remaining)
                    )
                    if not results:
                        break
                    drained += len(results)
            except Exception:
                callback_drain_failed = True
        if self._native_start_attempted and not self._native_stop_complete:
            if self._trader is None:
                self._set_phase("CLOSE_FAILED", "TRADER_REFERENCE_MISSING")
                raise CtpExecutionRuntimeError("TRADER_REFERENCE_MISSING")
            self._native_stop_attempted = True
            try:
                receipt = self._trader.stop_and_wait(self.config.stop_timeout_s)
            except Exception:
                self._set_phase("CLOSE_FAILED", "NATIVE_STOP_UNCERTAIN")
                raise CtpExecutionRuntimeError("NATIVE_STOP_UNCERTAIN") from None
            self._last_stop_receipt = receipt
            from bt_api_ctp.ctp.client import CtpNativeStopReceipt

            if (
                type(receipt) is not CtpNativeStopReceipt
                or type(receipt.connection_generation) is not int
                or receipt.connection_generation < 0
                or type(receipt.join_required) is not bool
                or type(receipt.join_completed) is not bool
                or type(receipt.native_released) is not bool
                or (receipt.thread_alive is not None and type(receipt.thread_alive) is not bool)
                or type(receipt.timed_out) is not bool
                or receipt.complete is not True
            ):
                self._set_phase("CLOSE_FAILED", "NATIVE_STOP_UNCERTAIN")
                raise CtpExecutionRuntimeError("NATIVE_STOP_UNCERTAIN")
            self._native_stop_complete = True

        if self._store is not None and not self._store_closed:
            if self._callback_owner is not None:
                if callback_drain_failed:
                    self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_FAILED")
                    raise CtpExecutionRuntimeError("CALLBACK_DRAIN_FAILED")
                if not self._callback_ingress_is_drained():
                    self._set_phase("CLOSE_FAILED", "CALLBACK_DRAIN_INCOMPLETE")
                    raise CtpExecutionRuntimeError("CALLBACK_DRAIN_INCOMPLETE")
            if self._owned_risk_bridge is not None and not self._risk_bridge_closed:
                try:
                    self._owned_risk_bridge.close()
                except Exception:
                    self._set_phase("CLOSE_FAILED", "RISK_BRIDGE_CLOSE_UNCERTAIN")
                    raise CtpExecutionRuntimeError("RISK_BRIDGE_CLOSE_UNCERTAIN") from None
                self._risk_bridge_closed = True
            try:
                self._store.close()
            except Exception:
                self._set_phase("CLOSE_FAILED", "STORE_CLOSE_UNCERTAIN")
                raise CtpExecutionRuntimeError("STORE_CLOSE_UNCERTAIN") from None
            self._store_closed = True
        self._closed = True
        self._set_phase("CLOSED")


class CtpExecutionResourceProvider:
    """Build a single ``execution/account_owner`` resource set on Host start."""

    def __init__(self, config: CtpExecutionRuntimeConfig) -> None:
        if type(config) is not CtpExecutionRuntimeConfig:
            raise TypeError("exact CTP execution runtime config is required")
        self.config = config
        self.last_runtime: CtpExecutionRuntime | None = None
        self._used = False
        self._lock = threading.Lock()

    def __call__(
        self,
        component: str,
        instance_id: str,
        options: Mapping[str, object],
        scope: Any,
    ) -> Any:
        from bt_api_py.service_components import ServiceResourceConstructionError

        if (
            component != "execution"
            or not isinstance(options, Mapping)
            or options.get("role") != "account_owner"
            or type(instance_id) is not str
            or not instance_id
            or scope != self.config.service_scope
        ):
            raise CtpExecutionRuntimeError("RUNTIME_COMPONENT_BINDING_INVALID")
        with self._lock:
            if self._used:
                raise CtpExecutionRuntimeError("RUNTIME_PROVIDER_ONE_SHOT")
            self._used = True
        runtime = CtpExecutionRuntime(self.config, instance_id)
        self.last_runtime = runtime
        try:
            return runtime.bootstrap()
        except ServiceResourceConstructionError:
            raise
        except CtpExecutionRuntimeError:
            raise
        except Exception:
            if runtime._has_owned_state():
                resources = runtime._make_resources(complete=False)
                runtime._resources = resources
                raise ServiceResourceConstructionError(resources) from None
            raise CtpExecutionRuntimeError("RUNTIME_BOOTSTRAP_FAILED") from None

