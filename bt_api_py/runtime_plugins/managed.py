"""Managed execution composition after config and capability pins are accepted."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from .catalog import LoadedCapabilities, RuntimePluginError
from .contracts import CAPABILITY_EXECUTION, CAPABILITY_MONITOR, CAPABILITY_RISK
from .fake_dispatch_authority import (
    FakeDispatchJournalError,
    ManagedFakeProviderJournalAuthority,
)
from .managed_recovery import (
    DurableManagedRecoveryCoordinator,
    ManagedRecoveryCoordinatorError,
    ManagedRecoveryReport,
)

_OFFLINE_FAKE_POLICY_BINDINGS = {
    (
        "example.013_3.sa_midfreq_simnow",
        "iteration41_managed_replay_fake_provider",
        "iteration41_managed_replay_fake_account",
        "iteration41.managed_replay.l2",
    ),
    (
        "example.ctp_options_simnow.mechanical_managed_replay_l2",
        "iteration41_ctp_mechanical_fake_provider",
        "iteration41_ctp_mechanical_fake_account",
        "iteration41.ctp-mechanical-replay.l2",
    ),
}


def _requires_sealed_instrument_metadata(contract: Any) -> bool:
    """Return whether this managed route could leave the offline fixture boundary.

    The one reviewed offline managed-replay shape remains a deterministic,
    zero-provider-I/O fixture and intentionally has no provider metadata
    requirement.  Every other managed-execution shape is treated as a
    potentially external write route: it must use the normalized, sealed
    admission path instead of the legacy ``quantity * price`` mapper.
    """

    if not bool(getattr(contract, "is_managed_execution", False)):
        return False
    return not (
        getattr(contract, "preset", None) == "replay"
        and getattr(contract, "environment", None) == "offline"
    )


def _typed_dispatch_resolution_contract(risk: Any) -> tuple[tuple[type, ...], Any]:
    """Require the account-bound proof and resolver API used by fake dispatch."""

    required_types = (
        "AccountScope",
        "DispatchClaimBinding",
        "DispatchEvidenceClass",
        "DispatchTerminalProof",
        "DispatchTerminalState",
        "DispatchTrackedOrderProof",
        "VerifiedDispatchResolution",
    )
    missing = [name for name in required_types if not isinstance(getattr(risk, name, None), type)]
    gate_type = getattr(risk, "DurableRiskGate", None)
    if not isinstance(gate_type, type) or not callable(
        getattr(gate_type, "resolve_dispatch_freeze", None)
    ):
        missing.append("DurableRiskGate.resolve_dispatch_freeze")
    evidence_class_type = getattr(risk, "DispatchEvidenceClass", None)
    simulation_evidence_class = getattr(evidence_class_type, "SIMULATION_JOURNAL", None)
    if not isinstance(evidence_class_type, type) or not isinstance(
        simulation_evidence_class, evidence_class_type
    ):
        missing.append("DispatchEvidenceClass.SIMULATION_JOURNAL")
    if missing:
        raise RuntimePluginError(
            "RISK_TYPED_DISPATCH_API_REQUIRED",
            "risk capability lacks the typed account-bound dispatch resolution API: "
            + ", ".join(missing),
        )
    return (
        (risk.DispatchTerminalProof, risk.DispatchTrackedOrderProof),
        simulation_evidence_class,
    )


@dataclass(frozen=True)
class ManagedExecutionRuntime:
    """A code-owned composition of independent execution, risk, and monitor packages.

    This object owns no provider client.  The caller injects a provider dispatch
    port for each submit, keeping provider ownership in the existing SDK/Broker
    adapter and ensuring that a missing capability cannot downgrade to direct.
    Its facade uses the shared risk gate's atomic dispatch claim, so direct
    facade submission cannot bypass the durable account freeze.  Direct facade
    submission does not publish the monitor fact or automatically release that
    freeze; callers use :meth:`submit` for the complete managed path.
    """

    contract: Any
    facade: Any
    execution_store: Any
    outbox: Any
    risk_gate: Any
    risk_scope: Any
    scope: Any
    execution: Any
    outbox_event_type: Any
    state_directory: Path | None = None
    instrument_admission: Any | None = None
    instrument_metadata_snapshot: Any | None = None
    recovery_coordinator: DurableManagedRecoveryCoordinator | None = None
    fake_dispatch_authority: ManagedFakeProviderJournalAuthority | None = None
    # This identity belongs to one composed Python runtime, rather than the
    # durable SDK writer lease.  A Backtrader projection receipt may be
    # applied once again after an actual process restart because the prior
    # framework position/order/observer state no longer exists; duplicate
    # callbacks within this process retain the same identity and are blocked.
    framework_projection_session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # Backtrader owns the projection sink, so this SDK module deliberately
    # stores only a generic closeable reference rather than importing that
    # package.  The composition root still owns shutdown ordering: close the
    # local framework receipt before closing the execution SQLite store that
    # provides its durable evidence.
    _framework_projection_closeables: tuple[Any, ...] = field(
        default=(), init=False, repr=False, compare=False
    )
    dispatch_resolution_proof_types: tuple[type, ...] = ()
    simulation_dispatch_evidence_class: Any | None = None

    def __post_init__(self) -> None:
        """Keep ad-hoc construction from weakening a managed-live composition."""

        if (
            not isinstance(self.framework_projection_session_id, str)
            or len(self.framework_projection_session_id) != 32
            or any(
                character not in "0123456789abcdef"
                for character in self.framework_projection_session_id
            )
        ):
            raise RuntimePluginError(
                "FRAMEWORK_PROJECTION_SESSION_INVALID",
                "managed runtime requires a fresh framework projection session identity",
            )

        if not _requires_sealed_instrument_metadata(self.contract):
            return
        from .instrument_risk import SealedNormalizedInstrumentMetadataSnapshot

        snapshot = self.instrument_metadata_snapshot
        if not isinstance(snapshot, SealedNormalizedInstrumentMetadataSnapshot):
            raise RuntimePluginError(
                "INSTRUMENT_SNAPSHOT_REQUIRED",
                "managed live runtime requires sealed instrument metadata",
            )
        snapshot.require_execution_scope(self.scope)
        snapshot.require_live_metadata()
        if getattr(self.instrument_admission, "snapshot", None) is not snapshot:
            raise RuntimePluginError(
                "INSTRUMENT_ADMISSION_REQUIRED",
                "managed live runtime requires admission bound to its sealed snapshot",
            )
        if self.recovery_coordinator is None:
            raise RuntimePluginError(
                "MANAGED_RECOVERY_COORDINATOR_REQUIRED",
                "managed live runtime requires durable recovery coordination",
            )

    def close(self) -> None:
        """Release the writer lease and close caller-owned local durability state.

        Composition has no network worker to stop, but the execution store owns
        a persistent SQLite connection.  Exposing its lifecycle here prevents a
        Windows process from retaining the journal file after a short-lived
        runtime or acceptance run finishes.
        """

        first_error: Exception | None = None
        for component in (
            *self._framework_projection_closeables,
            self.facade,
            self.outbox,
            self.execution_store,
            self.risk_gate,
            self.recovery_coordinator,
            self.fake_dispatch_authority,
        ):
            close = getattr(component, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        object.__setattr__(self, "_framework_projection_closeables", ())
        if first_error is not None:
            raise first_error

    def register_framework_projection_closeable(self, closeable: Any) -> None:
        """Register a Backtrader-owned local receipt for ordered shutdown.

        The execution runtime must not import or construct framework code.  A
        validated bridge can nevertheless register its local SQLite receipt so
        a normal runtime close releases the Windows file handle before cleanup.
        """

        if not callable(getattr(closeable, "close", None)):
            raise RuntimePluginError(
                "FRAMEWORK_PROJECTION_CLOSEABLE_INVALID",
                "framework projection closeable must expose close()",
            )
        current = self._framework_projection_closeables
        if any(existing is closeable for existing in current):
            return
        object.__setattr__(self, "_framework_projection_closeables", (*current, closeable))

    def submit(self, intent: Any, dispatcher: Callable[[Any], Any]) -> Any:
        """Submit once through the durable facade and publish a redacted fact.

        The runtime persists a unique account freeze immediately before the
        injected provider port can run.  A directly observed, settled provider
        outcome clears that freeze only after its monitor fact is durable.  A
        provider exception, unknown result, settlement failure, monitor-outbox
        failure, or process crash leaves the freeze active; callers must never
        retry that intent.
        """
        coordinator = self.recovery_coordinator
        if coordinator is None:
            return self._submit_without_recovery_coordinator(intent, dispatcher)

        # A restart cannot be treated as permission to resend a prepared
        # provider request.  Recover prior work before admitting a new intent;
        # this pass performs local journal/outbox work only, never provider I/O.
        self.recover()

        def mark_provider_dispatch(current_intent: Any) -> None:
            if current_intent.intent_id != intent.intent_id:
                raise RuntimePluginError(
                    "DISPATCH_IDENTITY_MISMATCH",
                    "facade pre-dispatch hook received another intent",
                )
            try:
                coordinator.prepare_dispatch(current_intent)
            except ManagedRecoveryCoordinatorError as error:
                raise RuntimePluginError(
                    "MANAGED_RECOVERY_PREPARE_FAILED",
                    "provider dispatch was not durably prepared by the recovery authority",
                ) from error

        active_dispatcher = dispatcher
        if self.fake_dispatch_authority is not None:

            def record_fake_provider_response(current_intent: Any) -> Any:
                submit = getattr(dispatcher, "submit", None)
                observation = (
                    submit(current_intent) if callable(submit) else dispatcher(current_intent)
                )
                self.fake_dispatch_authority.record_provider_observation(
                    current_intent, observation
                )
                return observation

            active_dispatcher = record_fake_provider_response
        record = self.facade.submit(
            intent, active_dispatcher, before_dispatch=mark_provider_dispatch
        )
        try:
            if self.fake_dispatch_authority is not None:
                self.fake_dispatch_authority.record_execution_result(intent, record)
            coordinator.record_result(intent, record)
            coordinator.append_pending_monitor_events(
                self.scope.key, self.outbox, self.outbox_event_type
            )
        except Exception as error:
            raise RuntimePluginError(
                "MONITOR_OUTBOX_UNCONFIRMED",
                "execution result exists but monitor delivery requires reconciliation",
            ) from error
        self._finish_coordinated_freeze_if_ready(intent.intent_id, record)
        return record

    def _submit_without_recovery_coordinator(
        self, intent: Any, dispatcher: Callable[[Any], Any]
    ) -> Any:
        """Preserve the explicit legacy/gateway composition behavior.

        Gateway composition owns a different server-side authority.  It may
        construct this runtime without the local direct-dispatch coordinator,
        so retain its established monitor and latch behavior until that
        separate path explicitly adopts the same journal.
        """

        provider_dispatch_imminent = False

        def mark_provider_dispatch(current_intent: Any) -> None:
            nonlocal provider_dispatch_imminent
            if current_intent.intent_id != intent.intent_id:
                raise RuntimePluginError(
                    "DISPATCH_IDENTITY_MISMATCH",
                    "facade pre-dispatch hook received another intent",
                )
            provider_dispatch_imminent = True

        record = self.facade.submit(intent, dispatcher, before_dispatch=mark_provider_dispatch)
        event_id = "execution." + intent.intent_id + "." + str(record.state.value).lower()
        try:
            self.outbox.append(
                self.outbox_event_type(
                    event_id=event_id,
                    scope=self.scope.key,
                    event_type="execution_state",
                    data={
                        "intent_id": intent.intent_id,
                        "scope_digest": self._scope_digest(self.scope.key),
                        "state": record.state.value,
                    },
                    occurred_at=record.updated_at_ns / 1_000_000_000,
                )
            )
        except Exception as error:
            raise RuntimePluginError(
                "MONITOR_OUTBOX_UNCONFIRMED",
                "execution result exists but monitor delivery requires reconciliation",
            ) from error
        if provider_dispatch_imminent and self._has_confirmed_settled_evidence(record):
            self._resolve_active_dispatch_freeze(intent.intent_id, reassert_on_failure=True)
        return record

    def recover(self) -> ManagedRecoveryReport:
        """Recover local prepared work without retrying any provider dispatch.

        The facade's existing scope writer lease fences concurrent runtimes.
        A crash after the coordinator's pre-dispatch commit is converted to a
        durable ``UNKNOWN`` execution state where possible, its account freeze
        is reasserted, and reconciliation remains required.  Known execution
        results only replay their deterministic monitor fact; a provider port
        is never touched here.
        """

        coordinator = self.recovery_coordinator
        if coordinator is None:
            return ManagedRecoveryReport((), (), (), ())
        writer_lease = self.facade.acquire_writer_lease()
        recovered_unknown: list[str] = []
        reconciliation_required: list[str] = []
        # The facade claims ``DISPATCHING`` before it invokes the coordinator's
        # pre-dispatch hook.  Discover the crash window where that hook never
        # committed its companion work row.  This is local journal work only;
        # it cannot call a provider or manufacture a retry permission.
        prepared_ids = {work.intent_id for work in coordinator.pending_work(self.scope.key)}
        for dispatching in self.execution_store.list_dispatching(self.scope):
            if dispatching.intent_id in prepared_ids:
                continue
            self._recover_untracked_dispatching_record(
                coordinator,
                dispatching,
                writer_lease,
                recovered_unknown,
                reconciliation_required,
            )
        for work in coordinator.pending_work(self.scope.key):
            try:
                intent = self.execution_store.get_intent(work.intent_id, scope=self.scope)
                record = self.facade.get(work.intent_id)
            except Exception:
                self._require_reconciliation_and_freeze(
                    coordinator, work.intent_id, "recovery_execution_record_unavailable"
                )
                reconciliation_required.append(work.intent_id)
                continue
            if intent is None or record is None or record.payload_sha256 != work.payload_sha256:
                self._require_reconciliation_and_freeze(
                    coordinator,
                    work.intent_id,
                    "recovery_execution_identity_unavailable",
                )
                reconciliation_required.append(work.intent_id)
                continue
            state_value = record.state.value
            if work.phase == "DISPATCH_PREPARED" and state_value == "DISPATCHING":
                try:
                    record = self.execution_store.mark_unknown(
                        work.intent_id,
                        self.scope,
                        "coordinator_recovery_unknown",
                        writer_lease=writer_lease,
                    )
                except Exception:
                    self._require_reconciliation_and_freeze(
                        coordinator,
                        work.intent_id,
                        "recovery_unknown_transition_failed",
                    )
                    reconciliation_required.append(work.intent_id)
                    continue
                recovered_unknown.append(work.intent_id)
            if (
                self.fake_dispatch_authority is not None
                and coordinator.freeze_status_for(self.scope.key, work.intent_id) != "RESOLVED"
            ):
                # The fake-provider journal attests the original dispatch and
                # its exact first SDK projection.  Once the risk freeze was
                # resolved, later lifecycle observations (for example a
                # separately observed cancellation) must not rewrite that
                # immutable dispatch attestation or be mistaken for new
                # provider evidence.  Unresolved work still requires an exact
                # projection match and fails closed on any state/ID/fill edit.
                try:
                    self.fake_dispatch_authority.record_execution_result(intent, record)
                except FakeDispatchJournalError:
                    self._require_reconciliation_and_freeze(
                        coordinator, work.intent_id, "fake_journal_projection_mismatch"
                    )
                    reconciliation_required.append(work.intent_id)
                    continue
            try:
                coordinator.record_result(intent, record)
            except ManagedRecoveryCoordinatorError as error:
                raise RuntimePluginError(
                    "MANAGED_RECOVERY_RESULT_UNAVAILABLE",
                    "execution result could not be durably linked to recovery work",
                ) from error
            if record.state.value == "UNKNOWN":
                self._require_reconciliation_and_freeze(
                    coordinator,
                    work.intent_id,
                    "coordinator_recovery_unknown"
                    if work.phase == "DISPATCH_PREPARED"
                    else "unknown_provider_outcome",
                )
                reconciliation_required.append(work.intent_id)
            elif record.review_required:
                # Reconciled evidence deliberately keeps this review latch.
                # The reconciliation-control port, rather than this automatic
                # restart pass, owns any reviewed freeze release.  Reasserting
                # here could undo a just-completed audited control release.
                reconciliation_required.append(work.intent_id)
            elif work.phase == "DISPATCH_PREPARED" and record.state.value not in {
                "ACKED",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELLED",
                "REJECTED",
            }:
                # A prepared row must never be silently downgraded by an
                # inconsistent local execution state.
                self._require_reconciliation_and_freeze(
                    coordinator, work.intent_id, "recovery_prepared_state_unproven"
                )
                reconciliation_required.append(work.intent_id)
        try:
            emitted = coordinator.append_pending_monitor_events(
                self.scope.key, self.outbox, self.outbox_event_type
            )
        except ManagedRecoveryCoordinatorError as error:
            raise RuntimePluginError(
                "MONITOR_OUTBOX_UNCONFIRMED",
                "recovery monitor delivery requires reconciliation",
            ) from error
        resolved: list[str] = []
        for work in coordinator.pending_work(self.scope.key):
            if coordinator.freeze_status_for(work.scope_key, work.intent_id) not in {
                "PENDING",
                "RELEASE_ATTEMPTED",
            }:
                continue
            record = self.facade.get(work.intent_id)
            if record is None or not self._has_confirmed_settled_evidence(record):
                continue
            self._finish_coordinated_freeze_if_ready(work.intent_id, record)
            resolved.append(work.intent_id)
        return ManagedRecoveryReport(
            tuple(recovered_unknown),
            tuple(dict.fromkeys(reconciliation_required)),
            emitted,
            tuple(resolved),
        )

    def _require_reconciliation_and_freeze(
        self,
        coordinator: DurableManagedRecoveryCoordinator,
        intent_id: str,
        reason: str,
    ) -> None:
        try:
            coordinator.require_reconciliation(
                scope_key=self.scope.key, intent_id=intent_id, reason=reason
            )
            self._ensure_dispatch_freeze(intent_id)
        except ManagedRecoveryCoordinatorError as error:
            raise RuntimePluginError(
                "MANAGED_RECOVERY_RECONCILIATION_LATCH_FAILED",
                "prepared provider dispatch could not be durably frozen for reconciliation",
            ) from error

    def _recover_untracked_dispatching_record(
        self,
        coordinator: DurableManagedRecoveryCoordinator,
        dispatching: Any,
        writer_lease: Any,
        recovered_unknown: list[str],
        reconciliation_required: list[str],
    ) -> None:
        """Fail closed when a crash preceded the recovery prepare hook.

        The durable execution claim proves that a provider dispatch might have
        started, even if the separate coordinator row was never committed.
        Convert it to ``UNKNOWN`` and reassert its named account freeze without
        calling any provider.  Missing/corrupt intent payloads remain frozen
        too; they simply cannot be linked to a typed monitor work item.
        """

        intent_id = getattr(dispatching, "intent_id", None)
        if not isinstance(intent_id, str) or not intent_id:
            raise RuntimePluginError(
                "MANAGED_RECOVERY_DISPATCHING_RECORD_INVALID",
                "dispatching execution record lacks a usable intent identity",
            )
        try:
            intent = self.execution_store.get_intent(intent_id, scope=self.scope)
        except Exception:
            intent = None
        if (
            intent is None
            or getattr(intent, "scope", None) != self.scope
            or getattr(intent, "fingerprint", None) != getattr(dispatching, "payload_sha256", None)
        ):
            try:
                self.execution_store.mark_unknown(
                    intent_id,
                    self.scope,
                    "recovery_prepare_identity_unavailable",
                    writer_lease=writer_lease,
                )
            except Exception as error:
                raise RuntimePluginError(
                    "MANAGED_RECOVERY_UNKNOWN_TRANSITION_FAILED",
                    "untracked dispatching execution could not become unknown",
                ) from error
            self._ensure_dispatch_freeze(intent_id)
            recovered_unknown.append(intent_id)
            reconciliation_required.append(intent_id)
            return
        try:
            coordinator.prepare_dispatch(intent)
            unknown = self.execution_store.mark_unknown(
                intent_id,
                self.scope,
                "recovery_prepare_hook_missing",
                writer_lease=writer_lease,
            )
            coordinator.record_result(intent, unknown)
        except ManagedRecoveryCoordinatorError as error:
            self._ensure_dispatch_freeze(intent_id)
            raise RuntimePluginError(
                "MANAGED_RECOVERY_PREPARE_DISCOVERY_FAILED",
                "untracked dispatching execution could not be linked for recovery",
            ) from error
        except Exception as error:
            self._ensure_dispatch_freeze(intent_id)
            raise RuntimePluginError(
                "MANAGED_RECOVERY_UNKNOWN_TRANSITION_FAILED",
                "untracked dispatching execution could not become unknown",
            ) from error
        self._require_reconciliation_and_freeze(
            coordinator, intent_id, "recovery_prepare_hook_missing"
        )
        recovered_unknown.append(intent_id)
        reconciliation_required.append(intent_id)

    def _ensure_dispatch_freeze(self, intent_id: str) -> None:
        """Verify an existing dispatch latch without fabricating one.

        Dispatch-inflight causes are reserved to the risk gate's atomic claim
        operation.  Recovery cannot safely recreate one through the generic
        freeze API after discovering a missing latch, so it reports that
        integrity gap and leaves the uncertain execution blocked.
        """

        cause_id = self._dispatch_freeze_cause(intent_id)
        try:
            self._assert_current_writer()
            active_reasons = self.risk_gate.active_freeze_reasons(self.risk_scope)
            if cause_id in active_reasons:
                return
        except Exception as error:
            raise RuntimePluginError(
                "MANAGED_RECOVERY_FREEZE_STATUS_UNAVAILABLE",
                "recovered provider dispatch latch could not be verified",
            ) from error
        raise RuntimePluginError(
            "MANAGED_RECOVERY_DISPATCH_FREEZE_MISSING",
            "risk gate cannot recreate a missing dispatch-inflight latch",
        )

    def _finish_coordinated_freeze_if_ready(self, intent_id: str, record: Any) -> None:
        """Resolve one known local latch only after its monitor fact is durable."""

        coordinator = self.recovery_coordinator
        if coordinator is None or not self._has_confirmed_settled_evidence(record):
            return
        try:
            status = coordinator.freeze_status_for(self.scope.key, intent_id)
        except ManagedRecoveryCoordinatorError as error:
            raise RuntimePluginError(
                "MANAGED_RECOVERY_FREEZE_STATUS_UNAVAILABLE",
                "dispatch-freeze resolution state could not be verified",
            ) from error
        if status == "RESOLVED":
            return
        if status not in {"PENDING", "RELEASE_ATTEMPTED"}:
            return
        self._assert_current_writer()
        self._ensure_coordinated_risk_settlement(intent_id, record)
        try:
            coordinator.mark_freeze_release_attempted(self.scope.key, intent_id)
        except ManagedRecoveryCoordinatorError as error:
            raise RuntimePluginError(
                "MANAGED_RECOVERY_FREEZE_STATUS_UNAVAILABLE",
                "dispatch-freeze resolution could not be durably prepared",
            ) from error
        cause_id = self._dispatch_freeze_cause(intent_id)
        try:
            self._resolve_active_dispatch_freeze(intent_id, reassert_on_failure=True)
            self._assert_current_writer()
            active_reasons = self.risk_gate.active_freeze_reasons(self.risk_scope)
            if cause_id in active_reasons:
                raise RuntimePluginError(
                    "DISPATCH_FREEZE_RESOLUTION_UNCONFIRMED",
                    "typed dispatch resolution left the risk latch active",
                )
        except Exception as error:
            self._reassert_dispatch_freeze(cause_id, error, True)
            raise RuntimePluginError(
                "DISPATCH_FREEZE_RESOLUTION_FAILED",
                "dispatch freeze could not be resolved",
            ) from error
        try:
            coordinator.mark_freeze_resolved(self.scope.key, intent_id)
        except ManagedRecoveryCoordinatorError as error:
            # The provider outcome and monitor fact remain known, but retain a
            # conservative local latch until a later recovery pass can finish.
            self._ensure_dispatch_freeze(intent_id)
            raise RuntimePluginError(
                "MANAGED_RECOVERY_FREEZE_STATUS_UNAVAILABLE",
                "dispatch-freeze resolution completion was not durable",
            ) from error

    def _ensure_coordinated_risk_settlement(self, intent_id: str, record: Any) -> None:
        """Prove the risk reservation cannot disappear before latch release.

        Execution and risk ownership use distinct SQLite databases.  A crash
        after the execution record reaches ``ACKED``/fill but before the risk
        permit settles must therefore be recoverable without relying on permit
        TTL.  The risk owner's idempotent proof accepts active or settled only;
        expired, released, or unknown permits leave the dispatch freeze active.
        """

        coordinator = self.recovery_coordinator
        if coordinator is None:
            return
        try:
            status = coordinator.risk_settlement_status_for(self.scope.key, intent_id)
        except ManagedRecoveryCoordinatorError as error:
            raise RuntimePluginError(
                "MANAGED_RECOVERY_RISK_SETTLEMENT_UNAVAILABLE",
                "risk-settlement proof state could not be verified",
            ) from error
        if status == "SETTLED":
            return
        if status != "PENDING":
            self._ensure_dispatch_freeze(intent_id)
            raise RuntimePluginError(
                "MANAGED_RECOVERY_RISK_SETTLEMENT_REQUIRED",
                "dispatch freeze cannot release without a pending risk-settlement proof",
            )
        permit_reference = getattr(record, "permit_reference", None)
        if not isinstance(permit_reference, str) or not permit_reference.strip():
            self._ensure_dispatch_freeze(intent_id)
            raise RuntimePluginError(
                "MANAGED_RECOVERY_RISK_SETTLEMENT_REQUIRED",
                "confirmed execution record lacks the risk permit reference",
            )
        settle = getattr(self.risk_gate, "ensure_settled", None)
        if not callable(settle):
            self._ensure_dispatch_freeze(intent_id)
            raise RuntimePluginError(
                "MANAGED_RECOVERY_RISK_SETTLEMENT_UNAVAILABLE",
                "loaded risk capability lacks idempotent settlement proof",
            )
        try:
            self._assert_current_writer()
            settle(permit_reference)
        except Exception as error:
            self._ensure_dispatch_freeze(intent_id)
            raise RuntimePluginError(
                "MANAGED_RECOVERY_RISK_SETTLEMENT_FAILED",
                "risk permit settlement is unproven; dispatch freeze remains active",
            ) from error
        try:
            coordinator.mark_risk_settlement_confirmed(self.scope.key, intent_id)
        except ManagedRecoveryCoordinatorError as error:
            self._ensure_dispatch_freeze(intent_id)
            raise RuntimePluginError(
                "MANAGED_RECOVERY_RISK_SETTLEMENT_UNAVAILABLE",
                "risk settlement proof could not be made durable",
            ) from error

    def resolve_confirmed_dispatch_freeze(self, intent_id: str) -> None:
        """Reject the retired unaudited manual freeze-release shortcut.

        A direct facade reconciliation can turn an ``UNKNOWN`` record into a
        terminal state, so inspecting its current fields is not a sufficient
        authorization to clear the account latch.  A deployment must use
        :meth:`create_reconciliation_control`, which requires immutable
        evidence, durable monitor facts, an identity-bound authorization
        callback, and an operator-control audit record.
        """

        del intent_id
        raise RuntimePluginError(
            "CONTROLLED_FREEZE_RELEASE_REQUIRED",
            "manual dispatch-freeze release requires the reconciliation control port",
        )

    def create_reconciliation_control(
        self,
        *,
        authorize: Callable[[Any], Any],
        clock: Callable[[], float] | None = None,
    ) -> Any:
        """Create the only public control path for an unknown-dispatch latch.

        Importing the control module is delayed until a deployment explicitly
        supplies its authorization adapter.  Normal replay and read-only
        runtime paths therefore retain no control-plane import or database
        side effects.
        """

        if self.state_directory is None:
            raise RuntimePluginError(
                "CONTROL_STATE_DIRECTORY_REQUIRED",
                "managed runtime was not composed with a control state directory",
            )
        from .reconcile_control import ManagedReconciliationControlPort

        return ManagedReconciliationControlPort(
            self,
            state_directory=self.state_directory,
            authorize=authorize,
            clock=clock,
        )

    def _has_confirmed_settled_evidence(self, record: Any) -> bool:
        """Return whether known, settled evidence can release one local latch."""

        confirmed_states = {
            self.execution.ExecutionState.ACKED,
            self.execution.ExecutionState.PARTIALLY_FILLED,
            self.execution.ExecutionState.FILLED,
            self.execution.ExecutionState.CANCELLED,
            self.execution.ExecutionState.REJECTED,
        }
        return (
            record is not None
            and record.state in confirmed_states
            and not record.review_required
            and record.permit_reference is not None
        )

    def _resolve_active_dispatch_freeze(
        self,
        intent_id: str,
        *,
        reassert_on_failure: bool = False,
    ) -> None:
        """Resolve one active latch through typed proof and a held writer fence."""

        cause_id = self._dispatch_freeze_cause(intent_id)
        try:
            self._assert_current_writer()
            active_reasons = self.risk_gate.active_freeze_reasons(self.risk_scope)
            if cause_id not in active_reasons:
                raise RuntimePluginError(
                    "DISPATCH_FREEZE_NOT_ACTIVE",
                    "dispatch freeze is not active for this intent",
                )
            if (
                self.risk_scope.provider != "fake"
                or self.risk_scope.environment != "offline"
                or self.fake_dispatch_authority is None
            ):
                raise RuntimePluginError(
                    "DISPATCH_FREEZE_RECONCILIATION_REQUIRED",
                    "dispatch freeze requires its offline fake journal authority",
                )
            if (
                not self.dispatch_resolution_proof_types
                or self.simulation_dispatch_evidence_class is None
            ):
                raise RuntimePluginError(
                    "DISPATCH_FREEZE_RECONCILIATION_REQUIRED",
                    "risk capability lacks the typed dispatch proof contract",
                )
            proof = self.fake_dispatch_authority.create_resolution_proof(intent_id, self.risk_gate)
            if type(proof) not in self.dispatch_resolution_proof_types:
                raise RuntimePluginError(
                    "DISPATCH_RESOLUTION_PROOF_INVALID",
                    "fake journal authority returned an unrecognized typed proof",
                )
            if (
                proof.scope != self.risk_scope
                or proof.intent_id != intent_id
                or proof.cause_id != cause_id
                or proof.evidence_class is not self.simulation_dispatch_evidence_class
            ):
                raise RuntimePluginError(
                    "DISPATCH_RESOLUTION_PROOF_MISMATCH",
                    "typed dispatch proof does not match the active fake account latch",
                )
            resolver = getattr(self.risk_gate, "resolve_dispatch_freeze", None)
            if not callable(resolver):
                raise RuntimePluginError(
                    "DISPATCH_FREEZE_RECONCILIATION_REQUIRED",
                    "risk capability lacks typed dispatch resolution",
                )
            # The risk gate calls the injected journal authority while it holds
            # the proof commit path. That authority validates the same SDK
            # writer lease and keeps its SQLite account fence through commit.
            resolver(proof)
            self._assert_current_writer()
            if cause_id in self.risk_gate.active_freeze_reasons(self.risk_scope):
                raise RuntimePluginError(
                    "DISPATCH_FREEZE_RESOLUTION_UNCONFIRMED",
                    "typed dispatch resolution left the risk latch active",
                )
        except Exception as error:
            self._reassert_dispatch_freeze(cause_id, error, reassert_on_failure)
            raise RuntimePluginError(
                "DISPATCH_FREEZE_RESOLUTION_FAILED",
                "dispatch freeze could not be resolved by typed journal proof",
            ) from error

    def _reassert_dispatch_freeze(
        self,
        cause_id: str,
        resolution_error: Exception | None,
        enabled: bool,
    ) -> None:
        """Retain an existing dispatch latch after failed proof verification.

        The risk gate owns dispatch-inflight causes and deliberately rejects
        the generic freeze API for them.  If the original latch disappeared,
        this composition has no safe repair operation and fails closed.
        """

        if not enabled:
            return
        try:
            self._assert_current_writer()
            active_reasons = self.risk_gate.active_freeze_reasons(self.risk_scope)
        except Exception as status_error:
            raise RuntimePluginError(
                "DISPATCH_FREEZE_STATUS_UNAVAILABLE",
                "dispatch freeze status could not be verified after failed resolution",
            ) from (resolution_error or status_error)
        if cause_id in active_reasons:
            return
        raise RuntimePluginError(
            "DISPATCH_FREEZE_MISSING",
            "risk gate cannot recreate a missing dispatch-inflight latch",
        ) from resolution_error

    def _assert_current_writer(self) -> None:
        """Fence every cross-store risk mutation with the execution authority."""

        try:
            writer_lease = self.facade.acquire_writer_lease()
            self.execution_store.assert_writer_lease(self.scope, writer_lease)
        except Exception as error:
            raise RuntimePluginError(
                "MANAGED_WRITER_FENCE_UNAVAILABLE",
                "managed runtime lost its execution writer authority",
            ) from error

    @staticmethod
    def _dispatch_freeze_cause(intent_id: str) -> str:
        return "dispatch-inflight:" + intent_id

    @staticmethod
    def _scope_digest(scope_key: str) -> str:
        """Keep the public redaction format compatible with Python 3.8."""

        return scope_key[6:] if scope_key.startswith("scope:") else scope_key


def compose_managed_execution(
    capabilities: LoadedCapabilities,
    *,
    state_directory: Path,
    provider: str,
    environment: str,
    account_ref: str,
    strategy_id: str,
    writer_id: str,
    policy_id: str,
    max_increase_notional: Decimal,
    max_increase_count: int,
    permit_ttl_seconds: float = 30.0,
    trading_day: str | None = None,
    instrument_metadata_snapshot: Any | None = None,
    instrument_clock_ns: Callable[[], int] | None = None,
) -> ManagedExecutionRuntime:
    """Create an explicit local stack for an already-approved managed contract.

    ``state_directory`` and all limits are supplied by reviewed operator code,
    not by a user-editable strategy config.  The function performs no network
    I/O and it never substitutes an unprotected or direct route on failure.
    """
    contract = capabilities.contract
    if not contract.is_managed_execution:
        raise RuntimePluginError(
            "MANAGED_CONTRACT_REQUIRED",
            "managed composition requires a sealed managed route",
        )
    if contract.preset == "managed_live_gateway":
        raise RuntimePluginError(
            "GATEWAY_DISPATCH_UNSUPPORTED",
            "managed_live_gateway requires a dedicated gateway dispatch port",
        )
    if strategy_id != contract.strategy_id:
        raise RuntimePluginError(
            "STRATEGY_SCOPE_MISMATCH",
            "managed scope strategy does not match the effective contract",
        )
    if environment != contract.environment:
        raise RuntimePluginError(
            "ENVIRONMENT_SCOPE_MISMATCH",
            "managed scope environment does not match the effective contract",
        )
    if _requires_sealed_instrument_metadata(contract):
        # Every managed route that can leave the explicitly offline replay
        # fixture must be bound to typed, sealed provider metadata.  Falling
        # through to the legacy quantity * price mapper would make lot,
        # multiplier, fee, FX, scope and provenance facts advisory at exactly
        # the route which may write externally.  Offline replay remains an
        # offline fixture and is not reinterpreted as live.
        from .instrument_risk import SealedNormalizedInstrumentMetadataSnapshot

        if instrument_metadata_snapshot is None:
            raise RuntimePluginError(
                "INSTRUMENT_SNAPSHOT_REQUIRED",
                "managed live composition requires sealed instrument metadata",
            )
        if not isinstance(instrument_metadata_snapshot, SealedNormalizedInstrumentMetadataSnapshot):
            raise RuntimePluginError(
                "INSTRUMENT_SNAPSHOT_INVALID",
                "managed live composition needs a sealed normalized metadata snapshot",
            )
    execution = capabilities.require(CAPABILITY_EXECUTION)
    risk = capabilities.require(CAPABILITY_RISK)
    monitor = capabilities.require(CAPABILITY_MONITOR)
    dispatch_resolution_proof_types, simulation_dispatch_evidence_class = (
        _typed_dispatch_resolution_contract(risk)
    )
    state_directory = Path(state_directory).resolve(strict=False)
    scope = execution.ExecutionScope(
        provider=provider,
        environment=environment,
        account_ref=account_ref,
        strategy_id=strategy_id,
        trading_day=trading_day,
    )
    if _requires_sealed_instrument_metadata(contract):
        # The snapshot digest is not enough when the provider's quantity
        # semantics are absent.  Live admission must know whether the intent
        # quantity is contracts, base units, or another canonical unit.
        instrument_metadata_snapshot.require_live_metadata()
    fake_scope_binding = (
        contract.mode == "simulation"
        and contract.preset == "replay"
        and contract.environment == "offline"
        and contract.order_route == "managed_execution"
        and (contract.strategy_id, provider, account_ref, policy_id)
        in _OFFLINE_FAKE_POLICY_BINDINGS
    )
    risk_scope = risk.AccountScope(
        provider="fake" if fake_scope_binding else provider,
        environment=environment,
        account_id=account_ref,
    )
    risk_policy = risk.RiskPolicy(
        policy_id=policy_id,
        max_increase_notional=max_increase_notional,
        max_increase_count=max_increase_count,
        permit_ttl_seconds=permit_ttl_seconds,
    )

    def map_intent(intent: Any) -> Any:
        if intent.position_effect.value == "OPEN":
            if intent.price is None:
                raise RuntimePluginError(
                    "RISK_NOTIONAL_UNPROVEN",
                    "managed opening needs reviewed executable notional",
                )
            action = risk.IntentAction.INCREASE
            notional = intent.quantity * intent.price
        else:
            action = risk.IntentAction.REDUCE
            notional = Decimal("0")
        return risk.RiskIntent(
            intent_id=intent.intent_id,
            scope=risk_scope,
            action=action,
            notional=notional,
            payload_fingerprint=intent.fingerprint,
        )

    # The risk gate is constructed before sealed instrument admission so it
    # can be injected into that admission.  Keep one narrow late-bound mapper
    # for the fake journal; after admission is built it points at that exact
    # mapper, so proof fingerprints match the claim created by the facade.
    selected_risk_mapper: dict[str, Any] = {"mapper": map_intent}

    def map_selected_risk_intent(intent: Any) -> Any:
        return selected_risk_mapper["mapper"](intent)

    execution_store = execution.SqliteExecutionStore(state_directory / "execution.sqlite3")
    fake_dispatch_authority = None
    if fake_scope_binding:
        fake_dispatch_authority = ManagedFakeProviderJournalAuthority(
            database_path=state_directory / "fake_dispatch.sqlite3",
            execution_scope=scope,
            risk_scope=risk_scope,
            execution_store=execution_store,
            facade=None,
            risk_types=risk,
            risk_intent_mapper=map_selected_risk_intent,
        )
        risk_gate = risk.DurableRiskGate(
            state_directory / "risk.sqlite3",
            risk_policy,
            execution_journal_authority=fake_dispatch_authority,
        )
    else:
        risk_gate = risk.DurableRiskGate(state_directory / "risk.sqlite3", risk_policy)

    admission = execution.SharedRiskAdmissionAdapter(risk_gate, map_intent)
    instrument_admission = None
    if instrument_metadata_snapshot is not None:
        # This import remains local so generic/replay composition does not
        # load the normalized metadata machinery unless a reviewed caller
        # explicitly binds one sealed snapshot.
        from .instrument_risk import compose_instrument_risk_admission

        instrument_admission = compose_instrument_risk_admission(
            capabilities,
            risk_gate=risk_gate,
            risk_scope=risk_scope,
            normalized_snapshot=instrument_metadata_snapshot,
            execution_scope=scope,
            clock_ns=instrument_clock_ns,
        )
        admission = instrument_admission.admission_gate
        selected_risk_mapper["mapper"] = instrument_admission.mapper
    facade = execution.ManagedExecutionFacade(
        execution_store,
        scope,
        writer_id=writer_id,
        admission_gate=admission,
    )
    if fake_dispatch_authority is not None:
        fake_dispatch_authority.bind_facade(facade)
    outbox = monitor.DurableOutbox(state_directory / "monitor.sqlite3")
    # This fourth local journal is the authority for cross-component recovery
    # work.  It does not claim a distributed transaction across the three
    # package-owned stores and it never encloses a provider call.
    recovery_coordinator = DurableManagedRecoveryCoordinator(
        state_directory / "managed_recovery.sqlite3"
    )
    return ManagedExecutionRuntime(
        contract=contract,
        facade=facade,
        execution_store=execution_store,
        outbox=outbox,
        risk_gate=risk_gate,
        risk_scope=risk_scope,
        scope=scope,
        execution=execution,
        outbox_event_type=monitor.OutboxEvent,
        state_directory=state_directory,
        instrument_admission=instrument_admission,
        instrument_metadata_snapshot=instrument_metadata_snapshot,
        recovery_coordinator=recovery_coordinator,
        fake_dispatch_authority=fake_dispatch_authority,
        dispatch_resolution_proof_types=dispatch_resolution_proof_types,
        simulation_dispatch_evidence_class=simulation_dispatch_evidence_class,
    )
