"""Experimental, read-only Execution evidence composition for one Risk core.

The slot and bound sources do not attest account, provider or write authority.
The host must not mutate private Python state. This is not a hostile-code
boundary, and no production admission guard delegates to this component.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field

from bt_api_risk import DispatchClaimBinding, DispatchTrackedOrderProof
from bt_api_risk.risk_order_journal import RiskOrderJournal, canonical_json, sha256_text
from bt_api_risk.risk_order_journal_contracts import (
    CancelResolutionTargetBindingV1,
    RiskOrderJournalError,
)
from bt_api_risk.risk_order_journal_storage import RiskOrderJournalStorage

from .service_execution_risk_journal_bridge import (
    RiskExecutionJournalBridge,
    _validate_cancel_resolution_source_binding,
)


@dataclass(frozen=True, eq=False)
class _SourceBindingOperation:
    """Local query identity; possession is not authorization."""

    owner: object
    scope: object
    key: tuple
    source: tuple


@dataclass(frozen=True)
class _SourceBinding:
    """Retain the first source and a single, irreversible local decision."""

    source: tuple
    operation: _SourceBindingOperation
    _decision: dict = field(default_factory=dict, compare=False)

    def status(self):
        return self._decision.get("decision", "PENDING")


class _EvidenceReadContext(RiskExecutionJournalBridge):
    """Explicit adapter for the existing evidence algorithms, not a facade."""

    def __init__(self, owner, core, manager, fence):
        self._owner = owner
        self._core = core
        self._manager = manager
        self._fence = fence
        self._cancel_resolution_sources = {}
        self._active_source = None
        self._active_key = None
        self._cancel_resolution_sources_lock = nullcontext()

    def _assert_source_current(self):
        if self._active_source is not None:
            actual = self._owner._confirmed_source(self._active_key)
            if actual is not self._active_source:
                raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_UNKNOWN")

    @contextmanager
    def _guard(self):
        self._owner._assert_installed()
        RiskOrderJournal.assert_borrowed_fence(self._core, self._manager, self._fence)
        try:
            yield
        finally:
            try:
                RiskOrderJournal.assert_borrowed_fence(self._core, self._manager, self._fence)
            finally:
                self._owner._assert_installed()
                self._assert_source_current()

    def bind_cancel_resolution_source(self, *args, **kwargs):
        raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_UNAVAILABLE")

    def _read_cancel_resolution_source(self, scope, *, execution_scope_key, cancel_id):
        with self._guard():
            key = (scope.key, execution_scope_key)
            binding = self._owner._confirmed_source(key)
            self._active_key = key
            self._active_source = binding
            self._cancel_resolution_sources = {key: binding.source}
            return super()._read_cancel_resolution_source(
                scope, execution_scope_key=execution_scope_key, cancel_id=cancel_id
            )

    def _read_cancel_target_risk_source(self, fence, source):
        if fence is not self._fence:
            raise RiskOrderJournalError("FENCE_BORROW_UNAVAILABLE")
        with self._guard():
            return super()._read_cancel_target_risk_source(fence, source)

    def _build_cancel_dispatch_resolution_proof(self, fence, **kwargs):
        if fence is not self._fence:
            raise RiskOrderJournalError("FENCE_BORROW_UNAVAILABLE")
        with self._guard():
            return super()._build_cancel_dispatch_resolution_proof(fence, **kwargs)

    def _connection(self):
        return RiskOrderJournalStorage._connection(self._core)

    @staticmethod
    def _assert_current_in(connection, fence):
        return RiskOrderJournal._assert_current_in(connection, fence)

    def _command_from_row(self, connection, row, scope):
        return RiskOrderJournal._command_from_row(self._core, connection, row, scope)

    def _event_from_row(self, row, scope):
        return RiskOrderJournal._event_from_row(self._core, row, scope)

    @staticmethod
    def _verify_event_chains(connection, row, scope, revision, *, target_revision):
        return RiskOrderJournal._verify_event_chains(
            connection, row, scope, revision, target_revision=target_revision
        )

    def _risk_dispatch_profile(self, provider):
        # Reuse the exact core's bound port; a portless core stays unavailable.
        return self._core._risk_dispatch_profile(provider)


class RiskExecutionEvidenceComposition:
    """One core-owned bridge; a manager and exact fence are required per read."""

    def __init__(self, core: RiskOrderJournal) -> None:
        if type(core) is not RiskOrderJournal:
            raise RiskOrderJournalError("EXECUTION_COMPOSITION_CORE_REQUIRED")
        self._core = core
        self._sources = {}
        RiskOrderJournal._bind_execution_composition(core, self)

    def _assert_installed(self) -> None:
        core = self._core
        if (type(self) is not RiskExecutionEvidenceComposition
                or type(core) is not RiskOrderJournal
                or not RiskOrderJournal._is_execution_composition_current(core, self)):
            raise RiskOrderJournalError("EXECUTION_COMPOSITION_UNAVAILABLE")

    def new_cancel_source_binding_operation(self, scope, execution_scope, wire_scope_template, store):
        """Create a source-fixed local identity without publishing or reading a store."""
        self._assert_installed()
        _validate_cancel_resolution_source_binding(scope, execution_scope, wire_scope_template, store)
        operation = _SourceBindingOperation(
            self, scope, (scope.key, execution_scope.key), (execution_scope, wire_scope_template, store)
        )
        self._assert_installed()
        return operation

    def cancel_source_binding_status(self, operation):
        """Return local status, never authorization.

        A query that is itself interrupted has no result and the caller must
        continue to treat the outcome as UNKNOWN.
        """
        if (type(operation) is not _SourceBindingOperation or operation.owner is not self
                or type(operation.key) is not tuple or type(operation.source) is not tuple
                or type(self._sources) is not dict):
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_OPERATION_INVALID")
        try:
            self._assert_installed()
            binding = self._sources.get(operation.key)
            if (type(binding) is not _SourceBinding or type(binding.source) is not tuple
                    or len(binding.source) != 3 or binding.operation is not operation
                    or binding.source is not operation.source or type(binding._decision) is not dict):
                return "UNKNOWN"
            status = binding.status()
            self._assert_installed()
            return status if status in ("PENDING", "COMMITTED", "UNKNOWN") else "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def bind_cancel_resolution_source(self, operation):
        """Publish one source decision; response loss requires a status query.

        The first atomic source publication defines the first source. If an
        attempt is interrupted before publication, status cannot distinguish
        an unstarted attempt from one still in flight. Its external owner must
        retain UNKNOWN and must not automatically try a different source.
        """
        self._assert_installed()
        if (type(operation) is not _SourceBindingOperation or operation.owner is not self
                or type(operation.key) is not tuple or len(operation.key) != 2
                or type(operation.source) is not tuple or len(operation.source) != 3
                or type(self._sources) is not dict):
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_OPERATION_INVALID")
        _validate_cancel_resolution_source_binding(operation.scope, *operation.source)
        if operation.key != (operation.scope.key, operation.source[0].key):
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_OPERATION_INVALID")
        key = operation.key
        candidate = _SourceBinding(operation.source, operation)
        try:
            # First shared state change: publish key, exact source and the
            # operation together in one CPython dict operation.
            existing = self._sources.setdefault(key, candidate)
            if existing is not candidate:
                if type(existing) is _SourceBinding and existing.operation is operation:
                    raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_OPERATION_USED")
                raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_REBOUND")
            self._assert_installed()
            if self._sources.get(key) is not candidate or candidate.status() != "PENDING":
                raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_UNKNOWN")
            # The single decision is the linearization point. No exception or
            # lost response may overwrite a COMMITTED decision with UNKNOWN.
            if candidate._decision.setdefault("decision", "COMMITTED") != "COMMITTED":
                raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_UNKNOWN")
            return operation
        except BaseException:
            candidate._decision.setdefault("decision", "UNKNOWN")
            raise

    def _confirmed_source(self, key):
        if type(self._sources) is not dict:
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_UNKNOWN")
        binding = self._sources.get(key)
        if binding is None:
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_NOT_BOUND")
        if (type(binding) is not _SourceBinding or type(binding.source) is not tuple
                or len(binding.source) != 3 or type(binding._decision) is not dict
                or type(binding.operation) is not _SourceBindingOperation
                or binding.operation.owner is not self or binding.operation.key != key
                or binding.operation.source is not binding.source):
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_UNKNOWN")
        if binding.status() != "COMMITTED":
            raise RiskOrderJournalError("CANCEL_EXECUTION_SOURCE_UNKNOWN")
        return binding

    @contextmanager
    def _read_under_fence(self, manager, fence):
        self._assert_installed()
        core = self._core
        RiskOrderJournal.assert_borrowed_fence(core, manager, fence)
        reader = _EvidenceReadContext(self, core, manager, fence)
        try:
            yield reader
        finally:
            try:
                RiskOrderJournal.assert_borrowed_fence(core, manager, fence)
            finally:
                self._assert_installed()
                reader._assert_source_current()

    def read_cancel_resolution_target_binding(
        self, manager, fence, *, execution_scope_key: str, cancel_id: str,
    ) -> CancelResolutionTargetBindingV1:
        with self._read_under_fence(manager, fence) as reader:
            source = reader._read_cancel_resolution_source(
                fence.scope, execution_scope_key=execution_scope_key, cancel_id=cancel_id
            )
            command, _proof, _claim = reader._read_cancel_target_risk_source(fence, source)
            return CancelResolutionTargetBindingV1(
                scope=fence.scope,
                execution_scope_key=source.scope.key,
                cancel_id=source.cancel_intent.cancel_id,
                target_intent_id=source.target_intent.intent_id,
                provider_order_id=source.cancel_intent.provider_order_id,
                principal_ref=command.principal_ref,
                risk_intent_id=command.risk_intent_id,
                permit_id=command.permit_id or "",
                order_request_sha256=command.order_request_sha256,
                wire_scope_sha256=sha256_text(
                    canonical_json(dict(source.owner_batch_body["wire_scope"]))
                ),
            )

    def create_cancel_dispatch_resolution_proof(
        self, manager, fence, *, claim, target_claim, target_order_proof,
        execution_scope_key: str, cancel_id: str,
    ):
        with self._read_under_fence(manager, fence) as reader:
            if type(claim) is not DispatchClaimBinding or claim.scope != fence.scope:
                raise RiskOrderJournalError("CANCEL_RESOLUTION_CLAIM_INVALID")
            source = reader._read_cancel_resolution_source(
                fence.scope, execution_scope_key=execution_scope_key, cancel_id=cancel_id
            )
            _command, journal_proof, journal_claim = reader._read_cancel_target_risk_source(
                fence, source
            )
            if (type(target_claim) is not DispatchClaimBinding or target_claim != journal_claim
                    or type(target_order_proof) is not DispatchTrackedOrderProof
                    or target_order_proof != journal_proof):
                raise RiskOrderJournalError("CANCEL_TARGET_RISK_RECEIPT_MISMATCH")
            return reader._build_cancel_dispatch_resolution_proof(
                fence, claim=claim, source=source, target_order_proof=journal_proof
            )
