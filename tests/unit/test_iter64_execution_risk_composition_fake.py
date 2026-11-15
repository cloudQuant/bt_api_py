"""Fake control-flow checks only: no real fence, store or source authority."""

from decimal import Decimal
import gc
import inspect
import json
import sys
import weakref
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from bt_api_base.service_protocol import WireScope
from bt_api_execution import CancelIntent, ExecutionScope, ExecutionState, OrderIntent, Side
from bt_api_execution import SqliteExecutionStore
from bt_api_execution.store import CancelRecord, CancelResolutionSourceV1, ExecutionRecord
from bt_api_risk import AccountScope
from bt_api_risk.risk_order_journal import RiskOrderJournal
from bt_api_risk.risk_order_journal_contracts import RiskOrderJournalError, RiskOrderWriterFence
from bt_api_risk.risk_order_journal_storage import RiskOrderJournalStorage
import bt_api_risk.risk_order_journal as journal_module

from bt_api_py.service_execution_risk_composition import (
    RiskExecutionEvidenceComposition, _SourceBindingOperation,
)


def bind(bridge, scope, execution_scope, wire, store):
    operation = bridge.new_cancel_source_binding_operation(scope, execution_scope, wire, store)
    bridge.bind_cancel_resolution_source(operation)
    return operation


@pytest.fixture
def case():
    core = RiskOrderJournal.__new__(RiskOrderJournal)
    core._initialize_execution_composition_slot()
    scope = AccountScope("PAPER", "risk-account", "simulation")
    execution_scope = ExecutionScope("PAPER", "simulation", "wire-account", "strategy")
    wire = WireScope("domain", account_ref="wire-account", strategy_ref="strategy")
    store = object.__new__(SqliteExecutionStore)
    fence = RiskOrderWriterFence(scope, "fake-owner", 1, "a" * 64)
    return core, scope, execution_scope, wire, store, fence


def source_for(execution_scope, wire, *, domain="domain"):
    cancel = CancelIntent(
        "cancel", execution_scope, "target", "provider-order",
        tags={"owner_cancel_strategy_ref": "strategy", "owner_cancel_run_id": "run"},
    )
    target = OrderIntent.limit(
        intent_id="target", scope=execution_scope, signal_id="signal", instrument="IF",
        side=Side.BUY, quantity=Decimal("1"), price=Decimal("10"),
    )
    cancel_record = CancelRecord(
        "cancel", execution_scope.key, "target", "provider-order", cancel.fingerprint,
        ExecutionState.CANCELLED, "permit", 1, None, False, 1, 2,
    )
    target_record = ExecutionRecord(
        "target", execution_scope.key, target.fingerprint, ExecutionState.CANCELLED,
        "provider-order", Decimal("0"), None, "target-permit", 1, None, False, 1, 2,
    )
    body = {"wire_scope": {
        "execution_domain_ref": domain, "account_ref": wire.account_ref, "account_refs": [],
        "strategy_ref": wire.strategy_ref, "run_id": "run", "manual_book_ref": None,
        "portfolio_ref": wire.portfolio_ref, "group_id": None, "leg_id": None,
    }}
    # This DTO is test input only, not evidence that the fake store is authoritative.
    return CancelResolutionSourceV1(
        execution_scope, cancel, cancel_record, (), target, target_record, (), body, "a" * 64,
    )


def test_one_bridge_per_exact_core_is_strong_and_does_not_open_guard(case):
    core, *_ = case
    bridge = RiskExecutionEvidenceComposition(core)
    reference = weakref.ref(bridge)
    del bridge
    gc.collect()
    assert RiskOrderJournal._is_execution_composition_current(core, reference())
    with pytest.raises(RiskOrderJournalError, match="ALREADY_BOUND"):
        RiskExecutionEvidenceComposition(core)
    with pytest.raises(RiskOrderJournalError, match="UNAVAILABLE"):
        with core.cancel_dispatch_resolution_guard(object(), claim=object()):
            pytest.fail("composition cannot authorize a production guard")


def test_source_binding_is_one_shot_and_requires_exact_operation(case):
    core, scope, execution_scope, wire, store, _ = case
    bridge = RiskExecutionEvidenceComposition(core)
    operation = bind(bridge, scope, execution_scope, wire, store)
    assert bridge.cancel_source_binding_status(operation) == "COMMITTED"
    with pytest.raises(RiskOrderJournalError, match="OPERATION_USED"):
        bridge.bind_cancel_resolution_source(operation)
    other_store = object.__new__(SqliteExecutionStore)
    other = bridge.new_cancel_source_binding_operation(scope, execution_scope, wire, other_store)
    with pytest.raises(RiskOrderJournalError, match="REBOUND"):
        bridge.bind_cancel_resolution_source(other)
    assert bridge.cancel_source_binding_status(other) == "UNKNOWN"
    assert bridge.cancel_source_binding_status(operation) == "COMMITTED"
    binding = bridge._sources[(scope.key, execution_scope.key)]
    assert binding.source[2] is store
    with pytest.raises(FrozenInstanceError):
        binding.source = (execution_scope, wire, object.__new__(SqliteExecutionStore))
    with pytest.raises(FrozenInstanceError):
        operation.source = (execution_scope, wire, object.__new__(SqliteExecutionStore))
    with pytest.raises(RiskOrderJournalError, match="OPERATION_INVALID"):
        bridge.cancel_source_binding_status(object())
    with pytest.raises(RiskOrderJournalError, match="BINDING_INVALID"):
        bind(bridge, scope, execution_scope, wire, object())
    with pytest.raises(RiskOrderJournalError, match="BINDING_INVALID"):
        bind(bridge, scope, execution_scope,
             WireScope("domain", account_ref="another", strategy_ref="strategy"), store)


def test_constructed_operation_cannot_bypass_source_validation(case):
    core, scope, execution_scope, wire, store, _ = case
    bridge = RiskExecutionEvidenceComposition(core)
    key = (scope.key, execution_scope.key)
    invalid_store = _SourceBindingOperation(bridge, scope, key, (execution_scope, wire, object()))
    with pytest.raises(RiskOrderJournalError, match="BINDING_INVALID"):
        bridge.bind_cancel_resolution_source(invalid_store)
    invalid_key = _SourceBindingOperation(bridge, scope, ("wrong", key[1]), (execution_scope, wire, store))
    with pytest.raises(RiskOrderJournalError, match="OPERATION_INVALID"):
        bridge.bind_cancel_resolution_source(invalid_key)
    assert key not in bridge._sources


def trace_binding_line(fragment, callback):
    function = RiskExecutionEvidenceComposition.bind_cancel_resolution_source
    lines, start = inspect.getsourcelines(function)
    matches = [start + offset for offset, line in enumerate(lines) if fragment in line]
    assert len(matches) == 1
    hit = []

    def trace(frame, event, arg):
        if (event == "line" and frame.f_code is function.__code__
                and frame.f_lineno == matches[0] and not hit):
            hit.append(True)
            callback()
        return trace

    return trace, hit


def test_interruption_before_first_publication_is_indeterminate(case, monkeypatch):
    core, scope, execution_scope, wire, store, _ = case
    bridge = RiskExecutionEvidenceComposition(core)
    monkeypatch.setattr(SqliteExecutionStore, "__getattribute__",
                        lambda *a, **k: pytest.fail("pre-publication must not access store"))
    operation = bridge.new_cancel_source_binding_operation(scope, execution_scope, wire, store)

    def interrupt():
        assert bridge.cancel_source_binding_status(operation) == "UNKNOWN"
        assert (scope.key, execution_scope.key) not in bridge._sources
        raise KeyboardInterrupt

    trace, hit = trace_binding_line("existing = self._sources.setdefault(key, candidate)", interrupt)
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        with pytest.raises(KeyboardInterrupt):
            bridge.bind_cancel_resolution_source(operation)
    finally:
        sys.settrace(previous)
    assert hit == [True]
    assert bridge.cancel_source_binding_status(operation) == "UNKNOWN"
    assert operation.source[2] is store
    # An external owner must not infer no execution or automatically choose
    # a different store from this pre-publication UNKNOWN.


def test_interrupt_before_commit_preserves_first_source_as_unknown(case, monkeypatch):
    core, scope, execution_scope, wire, store, fence = case
    bridge = RiskExecutionEvidenceComposition(core)
    operation = bridge.new_cancel_source_binding_operation(scope, execution_scope, wire, store)
    monkeypatch.setattr(RiskOrderJournal, "assert_borrowed_fence", lambda *a: None)
    monkeypatch.setattr(SqliteExecutionStore, "read_cancel_resolution_source_by_key_v1",
                        lambda *a, **k: pytest.fail("pending source cannot be read"))

    def interrupt():
        assert bridge.cancel_source_binding_status(operation) == "PENDING"
        published = bridge._sources[(scope.key, execution_scope.key)]
        assert published.operation is operation and published.source is operation.source
        with pytest.raises(RiskOrderJournalError, match="SOURCE_UNKNOWN"):
            bridge.read_cancel_resolution_target_binding(
                object(), fence, execution_scope_key=execution_scope.key, cancel_id="cancel")
        raise KeyboardInterrupt

    trace, hit = trace_binding_line("if existing is not candidate:", interrupt)
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        with pytest.raises(KeyboardInterrupt):
            bridge.bind_cancel_resolution_source(operation)
    finally:
        sys.settrace(previous)
    assert hit == [True]
    assert bridge.cancel_source_binding_status(operation) == "UNKNOWN"
    assert bridge._sources[(scope.key, execution_scope.key)].source[2] is store
    with pytest.raises(RiskOrderJournalError, match="SOURCE_UNKNOWN"):
        bridge.read_cancel_resolution_target_binding(
            object(), fence, execution_scope_key=execution_scope.key, cancel_id="cancel")
    with pytest.raises(RiskOrderJournalError, match="REBOUND"):
        bind(bridge, scope, execution_scope, wire, object.__new__(SqliteExecutionStore))


def test_response_lost_after_commit_is_queried_without_rollback(case, monkeypatch):
    core, scope, execution_scope, wire, store, fence = case
    bridge = RiskExecutionEvidenceComposition(core)
    operation = bridge.new_cancel_source_binding_operation(scope, execution_scope, wire, store)
    monkeypatch.setattr(RiskOrderJournal, "assert_borrowed_fence", lambda *a: None)
    monkeypatch.setattr(SqliteExecutionStore, "read_cancel_resolution_source_by_key_v1",
                        lambda *a, **k: source_for(execution_scope, wire, domain="wrong-domain"))

    def interrupt_after_read():
        assert bridge.cancel_source_binding_status(operation) == "COMMITTED"
        with pytest.raises(RiskOrderJournalError, match="BINDING_MISMATCH"):
            bridge.read_cancel_resolution_target_binding(
                object(), fence, execution_scope_key=execution_scope.key, cancel_id="cancel")
        raise KeyboardInterrupt

    trace, hit = trace_binding_line("return operation", interrupt_after_read)
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        with pytest.raises(KeyboardInterrupt):
            bridge.bind_cancel_resolution_source(operation)
    finally:
        sys.settrace(previous)
    assert hit == [True]
    assert bridge.cancel_source_binding_status(operation) == "COMMITTED"
    with pytest.raises(RiskOrderJournalError, match="OPERATION_USED"):
        bridge.bind_cancel_resolution_source(operation)


def test_callback_failure_before_commit_is_unknown(case, monkeypatch):
    core, scope, execution_scope, wire, store, _ = case
    bridge = RiskExecutionEvidenceComposition(core)
    operation = bridge.new_cancel_source_binding_operation(scope, execution_scope, wire, store)
    original = RiskExecutionEvidenceComposition._assert_installed
    calls = []

    def broken_after_publication(self):
        calls.append(1)
        if len(calls) == 2:
            assert self._sources[(scope.key, execution_scope.key)].status() == "PENDING"
            raise RuntimeError("fake post-publication failure")
        return original(self)

    monkeypatch.setattr(RiskExecutionEvidenceComposition, "_assert_installed", broken_after_publication)
    with pytest.raises(RuntimeError, match="post-publication"):
        bridge.bind_cancel_resolution_source(operation)
    monkeypatch.setattr(RiskExecutionEvidenceComposition, "_assert_installed", original)
    assert bridge.cancel_source_binding_status(operation) == "UNKNOWN"
    assert bridge._sources[(scope.key, execution_scope.key)].source[2] is store


def test_reentrant_competitor_does_not_replace_pending_source(case):
    core, scope, execution_scope, wire, store, _ = case
    bridge = RiskExecutionEvidenceComposition(core)
    operation = bridge.new_cancel_source_binding_operation(scope, execution_scope, wire, store)
    other_store = object.__new__(SqliteExecutionStore)
    other = bridge.new_cancel_source_binding_operation(scope, execution_scope, wire, other_store)

    def compete():
        assert bridge.cancel_source_binding_status(operation) == "PENDING"
        with pytest.raises(RiskOrderJournalError, match="REBOUND"):
            bridge.bind_cancel_resolution_source(other)
        assert bridge.cancel_source_binding_status(other) == "UNKNOWN"
        assert bridge._sources[(scope.key, execution_scope.key)].source[2] is store

    trace, hit = trace_binding_line("if existing is not candidate:", compete)
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        bridge.bind_cancel_resolution_source(operation)
    finally:
        sys.settrace(previous)
    assert hit == [True]
    assert bridge.cancel_source_binding_status(operation) == "COMMITTED"


def test_unknown_decision_wins_against_reentrant_commit(case):
    core, scope, execution_scope, wire, store, _ = case
    bridge = RiskExecutionEvidenceComposition(core)
    operation = bridge.new_cancel_source_binding_operation(scope, execution_scope, wire, store)

    def decide_unknown():
        binding = bridge._sources[(scope.key, execution_scope.key)]
        assert binding.status() == "PENDING"
        assert binding._decision.setdefault("decision", "UNKNOWN") == "UNKNOWN"

    trace, hit = trace_binding_line('if candidate._decision.setdefault("decision", "COMMITTED")', decide_unknown)
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        with pytest.raises(RiskOrderJournalError, match="SOURCE_UNKNOWN"):
            bridge.bind_cancel_resolution_source(operation)
    finally:
        sys.settrace(previous)
    assert hit == [True]
    assert bridge.cancel_source_binding_status(operation) == "UNKNOWN"
    assert bridge._sources[(scope.key, execution_scope.key)].source[2] is store


def test_pid_change_makes_query_unknown_and_read_unavailable(case, monkeypatch):
    core, scope, execution_scope, wire, store, fence = case
    bridge = RiskExecutionEvidenceComposition(core)
    operation = bind(bridge, scope, execution_scope, wire, store)
    pid = core._execution_composition_slot.pid
    monkeypatch.setattr(journal_module, "os", SimpleNamespace(getpid=lambda: pid + 1))
    assert bridge.cancel_source_binding_status(operation) == "UNKNOWN"
    with pytest.raises(RiskOrderJournalError, match="COMPOSITION_UNAVAILABLE"):
        bridge.read_cancel_resolution_target_binding(
            object(), fence, execution_scope_key=execution_scope.key, cancel_id="cancel")
    monkeypatch.setattr(journal_module, "os", SimpleNamespace(getpid=lambda: pid))
    assert bridge.cancel_source_binding_status(operation) == "COMMITTED"


def test_operation_identity_cannot_be_reused_by_another_core(case):
    core, scope, execution_scope, wire, store, _ = case
    first = RiskExecutionEvidenceComposition(core)
    operation = bind(first, scope, execution_scope, wire, store)
    other_core = RiskOrderJournal.__new__(RiskOrderJournal)
    other_core._initialize_execution_composition_slot()
    second = RiskExecutionEvidenceComposition(other_core)
    with pytest.raises(RiskOrderJournalError, match="OPERATION_INVALID"):
        second.cancel_source_binding_status(operation)
    with pytest.raises(RiskOrderJournalError, match="OPERATION_INVALID"):
        second.bind_cancel_resolution_source(operation)
    assert first.cancel_source_binding_status(operation) == "COMMITTED"


def test_detached_operation_does_not_leave_source_readable(case, monkeypatch):
    core, scope, execution_scope, wire, store, fence = case
    bridge = RiskExecutionEvidenceComposition(core)
    operation = bind(bridge, scope, execution_scope, wire, store)
    bridge._sources[(scope.key, execution_scope.key)] = object()
    assert bridge.cancel_source_binding_status(operation) == "UNKNOWN"
    monkeypatch.setattr(RiskOrderJournal, "assert_borrowed_fence", lambda *args: None)
    monkeypatch.setattr(SqliteExecutionStore, "read_cancel_resolution_source_by_key_v1",
                        lambda *a, **k: pytest.fail("detached source cannot be read"))
    with pytest.raises(RiskOrderJournalError, match="SOURCE_UNKNOWN"):
        bridge.read_cancel_resolution_target_binding(
            object(), fence, execution_scope_key=execution_scope.key, cancel_id="cancel")


def test_fake_manager_and_fence_do_not_grant_source_access(case, monkeypatch):
    core, scope, execution_scope, wire, store, fence = case
    bridge = RiskExecutionEvidenceComposition(core)
    operation = bind(bridge, scope, execution_scope, wire, store)
    assert bridge.cancel_source_binding_status(operation) == "COMMITTED"
    monkeypatch.setattr(SqliteExecutionStore, "read_cancel_resolution_source_by_key_v1",
                        lambda *a, **k: pytest.fail("an unheld fence cannot authorize a read"))
    with pytest.raises(RiskOrderJournalError, match="FENCE_BORROW_UNAVAILABLE"):
        bridge.read_cancel_resolution_target_binding(
            object(), fence, execution_scope_key=execution_scope.key, cancel_id="cancel")
    with pytest.raises(RiskOrderJournalError, match="UNAVAILABLE"):
        with core.cancel_dispatch_resolution_guard(object(), claim=object()):
            pytest.fail("a source decision cannot authorize a guard")


def test_sdk_subclass_is_not_exact_core_and_cannot_enter_composition(case):
    from bt_api_py.service_risk_order_journal import RiskOrderJournal as SdkJournal

    _core, scope, *_ = case
    legacy = object.__new__(SdkJournal)
    assert type(legacy) is not RiskOrderJournal
    assert journal_module._trusted_fence_context(legacy, scope) is False
    with pytest.raises(RiskOrderJournalError, match="CORE_REQUIRED"):
        RiskExecutionEvidenceComposition(legacy)


def test_missing_source_and_changed_core_remain_unavailable(case, monkeypatch):
    core, scope, execution_scope, wire, store, fence = case
    bridge = RiskExecutionEvidenceComposition(core)
    monkeypatch.setattr(RiskOrderJournal, "assert_borrowed_fence", lambda *args: None)
    with pytest.raises(RiskOrderJournalError, match="NOT_BOUND"):
        bridge.read_cancel_resolution_target_binding(
            object(), fence, execution_scope_key=execution_scope.key, cancel_id="cancel"
        )
    bind(bridge, scope, execution_scope, wire, store)
    other = RiskOrderJournal.__new__(RiskOrderJournal)
    other._initialize_execution_composition_slot()
    bridge._core = other
    with pytest.raises(RiskOrderJournalError, match="COMPOSITION_UNAVAILABLE"):
        bridge.read_cancel_resolution_target_binding(
            object(), fence, execution_scope_key=execution_scope.key, cancel_id="cancel"
        )


def test_invalid_route_still_reads_bound_store_and_rechecks_fence(case, monkeypatch):
    core, scope, execution_scope, wire, store, fence = case
    bridge = RiskExecutionEvidenceComposition(core)
    bind(bridge, scope, execution_scope, wire, store)
    manager = object()
    checks = []
    reads = []

    def assert_fence(actual_core, actual_manager, actual_fence):
        assert actual_core is core and actual_manager is manager and actual_fence is fence
        checks.append("fence")

    def read(actual_store, cancel_id, *, scope_key):
        assert actual_store is store and cancel_id == "cancel" and scope_key == execution_scope.key
        reads.append("source")
        return source_for(execution_scope, wire, domain="wrong-domain")

    monkeypatch.setattr(RiskOrderJournal, "assert_borrowed_fence", assert_fence)
    monkeypatch.setattr(SqliteExecutionStore, "read_cancel_resolution_source_by_key_v1", read)
    with pytest.raises(RiskOrderJournalError, match="BINDING_MISMATCH"):
        bridge.read_cancel_resolution_target_binding(
            manager, fence, execution_scope_key=execution_scope.key, cancel_id="cancel"
        )
    assert reads == ["source"]
    assert checks == ["fence"] * 4


def test_wrong_fence_fails_before_source_and_postcheck_failure_cannot_succeed(case, monkeypatch):
    core, scope, execution_scope, wire, store, fence = case
    bridge = RiskExecutionEvidenceComposition(core)
    bind(bridge, scope, execution_scope, wire, store)
    manager = object()
    checks = []
    reads = []

    def assert_fence(actual_core, actual_manager, actual_fence):
        checks.append(1)
        if actual_core is not core or actual_manager is not manager or actual_fence is not fence or len(checks) == 4:
            raise RiskOrderJournalError("FENCE_BORROW_UNAVAILABLE")

    def read(*args, **kwargs):
        reads.append(1)
        return source_for(execution_scope, wire, domain="wrong-domain")

    monkeypatch.setattr(RiskOrderJournal, "assert_borrowed_fence", assert_fence)
    monkeypatch.setattr(SqliteExecutionStore, "read_cancel_resolution_source_by_key_v1", read)
    with pytest.raises(RiskOrderJournalError, match="FENCE_BORROW_UNAVAILABLE"):
        bridge.read_cancel_resolution_target_binding(
            object(), fence, execution_scope_key=execution_scope.key, cancel_id="cancel"
        )
    assert reads == []
    checks.clear()
    with pytest.raises(RiskOrderJournalError, match="FENCE_BORROW_UNAVAILABLE"):
        bridge.read_cancel_resolution_target_binding(
            manager, fence, execution_scope_key=execution_scope.key, cancel_id="cancel"
        )
    assert reads == [1]


def test_missing_risk_target_command_is_rejected(case, monkeypatch):
    core, scope, execution_scope, wire, store, fence = case
    bridge = RiskExecutionEvidenceComposition(core)
    bind(bridge, scope, execution_scope, wire, store)
    manager = object()
    checks = []
    state = []

    class FakeRows:
        def fetchall(self):
            return []

    class FakeConnection:
        def execute(self, sql, values):
            assert "risk_order_commands" in sql
            state.append("read")
            return FakeRows()

        def close(self):
            state.append("close")

    def assert_fence(actual_core, actual_manager, actual_fence):
        assert actual_core is core and actual_manager is manager and actual_fence is fence
        checks.append(1)

    connection = FakeConnection()
    monkeypatch.setattr(RiskOrderJournal, "assert_borrowed_fence", assert_fence)
    monkeypatch.setattr(RiskOrderJournalStorage, "_connection", lambda actual_core: connection)
    monkeypatch.setattr(RiskOrderJournal, "_assert_current_in", staticmethod(lambda conn, item: None))
    monkeypatch.setattr(
        SqliteExecutionStore, "read_cancel_resolution_source_by_key_v1",
        lambda *args, **kwargs: source_for(execution_scope, wire),
    )
    with pytest.raises(RiskOrderJournalError, match="CANCEL_TARGET_RISK_COMMAND_AMBIGUOUS"):
        bridge.read_cancel_resolution_target_binding(
            manager, fence, execution_scope_key=execution_scope.key, cancel_id="cancel"
        )
    assert state == ["read", "close"]
    assert checks == [1] * 6


def test_corrupt_risk_event_chain_is_rejected(case, monkeypatch):
    core, scope, execution_scope, wire, store, fence = case
    bridge = RiskExecutionEvidenceComposition(core)
    bind(bridge, scope, execution_scope, wire, store)
    source = source_for(execution_scope, wire)
    manager = object()
    checks = []
    event = {
        "principal_ref": "principal", "intent_id": "target", "revision": 1,
        "event_type": "ACKED_TRACKED", "payload_json": json.dumps({
            "permit_id": "target-permit", "created_at_ns": 1, "updated_at_ns": 2,
        }),
        "previous_account_sha256": "f" * 64, "previous_command_sha256": "0" * 64,
        "record_sha256": "b" * 64, "writer_owner_id": "fake-owner",
        "writer_fencing_token": 1, "writer_fence_sha256": "a" * 64,
    }
    row = {
        "scope_key": scope.key, "principal_ref": "principal", "intent_id": "target",
        "risk_intent_id": "risk-target", "payload_sha256": "b" * 64,
        "canonical_payload_json": "{}", "wire_scope_json": json.dumps(source.owner_batch_body["wire_scope"]),
        "exchange_name": "fake", "provider_account_id": "fake", "client_order_id": "client",
        "order_request_json": "{}", "order_request_sha256": "c" * 64,
        "created_revision": 1, "created_record_sha256": "d" * 64,
    }

    class FakeRows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class FakeConnection:
        def execute(self, sql, values):
            if "risk_order_commands" in sql:
                return FakeRows([row])
            if sql.startswith("SELECT revision FROM risk_order_events"):
                return FakeRows([{"revision": 1}])
            assert "risk_order_events" in sql
            return FakeRows([event])

        def close(self):
            pass

    def assert_fence(actual_core, actual_manager, actual_fence):
        assert actual_core is core and actual_manager is manager and actual_fence is fence
        checks.append(1)

    monkeypatch.setattr(RiskOrderJournal, "assert_borrowed_fence", assert_fence)
    monkeypatch.setattr(RiskOrderJournalStorage, "_connection", lambda actual_core: FakeConnection())
    monkeypatch.setattr(RiskOrderJournal, "_assert_current_in", staticmethod(lambda conn, item: None))
    monkeypatch.setattr(SqliteExecutionStore, "read_cancel_resolution_source_by_key_v1", lambda *a, **k: source)
    with pytest.raises(RiskOrderJournalError, match="CANCEL_TARGET_RISK_CHAIN_INVALID"):
        bridge.read_cancel_resolution_target_binding(
            manager, fence, execution_scope_key=execution_scope.key, cancel_id="cancel"
        )
    assert checks == [1] * 6


def test_untyped_claim_cannot_request_proof(case, monkeypatch):
    core, scope, execution_scope, wire, store, fence = case
    bridge = RiskExecutionEvidenceComposition(core)
    bind(bridge, scope, execution_scope, wire, store)
    monkeypatch.setattr(RiskOrderJournal, "assert_borrowed_fence", lambda *args: None)
    with pytest.raises(RiskOrderJournalError, match="CLAIM_INVALID"):
        bridge.create_cancel_dispatch_resolution_proof(
            object(), fence, claim=object(), target_claim=object(), target_order_proof=object(),
            execution_scope_key=execution_scope.key, cancel_id="cancel",
        )
