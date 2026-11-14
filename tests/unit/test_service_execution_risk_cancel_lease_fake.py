"""Pure-fake checks for the cancel projection's Execution writer lease."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from bt_api_execution import CancelIntent, ExecutionScope, ExecutionState, SqliteExecutionStore
from bt_api_execution.store import CancelRecord, WriterLease
from bt_api_risk import (
    CancelDispatchResolutionReceipt,
    CancelDispatchResolutionReceiptStatus,
    DispatchResolutionReceipt,
    DispatchResolutionReceiptStatus,
    DispatchTrackedOrderProof,
)

from bt_api_py.service_execution_risk_cancel import (
    RiskExecutionCancelAdmissionError,
    RiskExecutionCancelAdmissionGate,
)


def _unvalidated_instance(cls, **fields):
    """Model already checked source data without constructing a real journal."""
    instance = object.__new__(cls)
    for name, value in fields.items():
        object.__setattr__(instance, name, value)
    return instance


def _case(monkeypatch, *, fail_check=None, already_resolved=False):
    scope = ExecutionScope("PAPER", "simulation", "fake-account", "fake-strategy", "20261018")
    intent = CancelIntent("cancel-1", scope, "target-1", "provider-1")
    record = CancelRecord(
        "cancel-1",
        scope.key,
        "target-1",
        "provider-1",
        "digest",
        ExecutionState.CANCELLED,
        "permit-1",
        1,
        None,
        False,
        1,
        1,
    )
    lease = WriterLease(scope.account_key, "writer", 1, 100)
    store = object.__new__(SqliteExecutionStore)
    events = []
    claim = SimpleNamespace()
    proof = SimpleNamespace(fingerprint="cancel-proof")
    target_claim = SimpleNamespace(permit_id="target-permit", intent_id="risk-target")
    tracked = _unvalidated_instance(DispatchTrackedOrderProof, provider_order_id="provider-1")
    monkeypatch.setattr(
        DispatchTrackedOrderProof, "fingerprint", property(lambda _self: "target-proof")
    )
    target_receipt = _unvalidated_instance(
        DispatchResolutionReceipt,
        status=DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED,
        claim=target_claim,
        proof=tracked,
        proof_sha256="target-proof",
        dispatch_latch_active=False,
    )
    active = _unvalidated_instance(
        CancelDispatchResolutionReceipt,
        status=CancelDispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE,
        claim=claim,
        proof=None,
    )
    resolved = _unvalidated_instance(
        CancelDispatchResolutionReceipt,
        status=CancelDispatchResolutionReceiptStatus.TERMINAL_RESOLVED,
        claim=claim,
        proof=proof,
        proof_sha256="cancel-proof",
        dispatch_latch_active=False,
    )

    def check(actual_store, actual_scope, actual_lease):
        assert actual_store is store
        assert actual_scope is scope
        assert actual_lease is lease
        events.append("lease")
        if events.count("lease") == fail_check:
            raise RuntimeError("lease unavailable")

    def read(actual_store, cancel_id, *, scope_key):
        assert actual_store is store and cancel_id == intent.cancel_id and scope_key == scope.key
        events.append("source")
        return SimpleNamespace(cancel_intent=intent, cancel_record=record)

    monkeypatch.setattr(SqliteExecutionStore, "assert_writer_lease", check)
    monkeypatch.setattr(SqliteExecutionStore, "read_cancel_resolution_source_by_key_v1", read)
    monkeypatch.setattr(RiskExecutionCancelAdmissionGate, "_risk_intent", lambda *_: None)
    monkeypatch.setattr(
        RiskExecutionCancelAdmissionGate, "_validate_cancel_receipt", lambda *_: None
    )

    class FakeGate:
        committed = already_resolved

        def cancel_dispatch_resolution_receipt(self, _permit):
            events.append("receipt")
            return resolved if self.committed else active

        def dispatch_resolution_receipt(self, _permit):
            return target_receipt

        def resolve_cancel_dispatch_inflight(self, actual_proof):
            assert actual_proof is proof
            events.append("commit")
            self.committed = True

    class FakeJournal:
        @contextmanager
        def writer_fence(self, _scope):
            yield object()

        def verify_cancel_dispatch_resolution_source(self, *_args, **_kwargs):
            return True

        def read_cancel_resolution_target_binding(self, *_args, **_kwargs):
            return SimpleNamespace(
                permit_id="target-permit",
                risk_intent_id="risk-target",
                principal_ref="principal",
                target_intent_id="target-1",
            )

        def has_resolved_ack_history(self, *_args, **_kwargs):
            return True

        def create_cancel_dispatch_resolution_proof(self, *_args, **_kwargs):
            return proof

    adapter = object.__new__(RiskExecutionCancelAdmissionGate)
    adapter._store = store
    adapter._gate = FakeGate()
    adapter._journal = FakeJournal()
    adapter._execution_scope = scope
    adapter._risk_scope = object()
    return SimpleNamespace(
        adapter=adapter,
        intent=intent,
        record=record,
        lease=lease,
        events=events,
        resolved=resolved,
    )


@pytest.mark.parametrize("fail_check,commits", [(1, 0), (2, 0), (3, 1), (4, 1)])
def test_lease_failure_never_retries_or_reports_success(monkeypatch, fail_check, commits):
    case = _case(monkeypatch, fail_check=fail_check)
    with pytest.raises(
        RiskExecutionCancelAdmissionError, match="cancel_terminal_resolution_unknown"
    ):
        case.adapter.resolve_terminal("permit-1", case.intent, case.record, case.lease)
    assert case.events.count("commit") == commits
    assert case.adapter._gate.committed is bool(commits)
    if fail_check == 1:
        assert "source" not in case.events
    if fail_check == 3:
        assert case.events.count("receipt") == 1


def test_lease_checks_surround_mutation_and_completed_readback(monkeypatch):
    case = _case(monkeypatch)
    assert (
        case.adapter.resolve_terminal("permit-1", case.intent, case.record, case.lease)
        is case.resolved
    )
    assert case.events == [
        "lease",
        "source",
        "receipt",
        "lease",
        "commit",
        "lease",
        "receipt",
        "lease",
    ]


def test_existing_resolution_requires_valid_lease_before_and_after_verification(monkeypatch):
    case = _case(monkeypatch, fail_check=2, already_resolved=True)
    with pytest.raises(
        RiskExecutionCancelAdmissionError, match="cancel_terminal_resolution_unknown"
    ):
        case.adapter.resolve_terminal("permit-1", case.intent, case.record, case.lease)
    assert case.events == ["lease", "source", "receipt", "lease"]
    assert case.adapter._gate.committed


@pytest.mark.parametrize("invalid_row", ["missing", "new_fence", "expired"])
def test_native_store_verifier_detects_lost_lease_after_commit(monkeypatch, invalid_row):
    native_assert = SqliteExecutionStore.assert_writer_lease
    case = _case(monkeypatch)
    monkeypatch.setattr(SqliteExecutionStore, "assert_writer_lease", native_assert)
    monkeypatch.setattr("bt_api_execution.store.time.time_ns", lambda: 50)
    checks = []

    class Cursor:
        def execute(self, sql, args=()):
            self.sql = sql
            if "execution_writer_leases" in sql:
                assert args == (case.intent.scope.account_key,)
            return self

        def fetchall(self):
            assert "ctp_account_store_identity" in self.sql
            return []

        def fetchone(self):
            assert "execution_writer_leases" in self.sql
            if len(checks) == 3:
                if invalid_row == "missing":
                    return None
                if invalid_row == "new_fence":
                    return {"owner_id": "writer", "fencing_token": 2, "expires_at_ns": 100}
                return {"owner_id": "writer", "fencing_token": 1, "expires_at_ns": 50}
            return {"owner_id": "writer", "fencing_token": 1, "expires_at_ns": 100}

    @contextmanager
    def transaction(actual_store):
        assert actual_store is case.adapter._store
        checks.append("check")
        yield Cursor()

    monkeypatch.setattr(SqliteExecutionStore, "_transaction", transaction)
    with pytest.raises(
        RiskExecutionCancelAdmissionError, match="cancel_terminal_resolution_unknown"
    ):
        case.adapter.resolve_terminal("permit-1", case.intent, case.record, case.lease)
    assert len(checks) == 3
    assert case.events.count("commit") == 1
    assert case.events.count("receipt") == 1
