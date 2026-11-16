"""SDK facade ports and CTP dispatch-bridge composition (fake provider leaves)."""
from __future__ import annotations

import pytest

from bt_api_risk.risk_order_journal import RiskOrderJournal as CoreRiskOrderJournal
from bt_api_risk.risk_order_journal_contracts import RiskOrderJournalError

from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_py.service_execution_risk_composition import _EvidenceReadContext

import test_ctp_execution_runtime as runtime_fixtures


def test_facade_owns_one_exact_core_and_binds_the_real_ports(tmp_path):
    journal = RiskOrderJournal(tmp_path / "journal.sqlite3", writer_id="ports-facade")

    assert type(journal) is RiskOrderJournal
    assert type(journal.core) is CoreRiskOrderJournal
    slot = journal.core._execution_composition_slot
    binding = slot.bindings["ports"]
    assert binding[0] == slot.pid
    assert binding[1] is slot.process_identity
    assert [entry[0].__class__.__name__ for entry in binding[2]] == [
        "_DispatchProfilePort", "_GatewayPositionSourcePort", "_CancelResolutionSourcePort",
    ]

    # The real SDK provider declaration is reached through the core port.
    profile = journal.core._risk_dispatch_profile("ctp")
    assert profile.provider == "ctp"

    # The real Gateway verifier is reached and fails closed on a non-receipt.
    with pytest.raises(RiskOrderJournalError, match="GROSS_BASELINE_POSITION_SOURCE_REQUIRED"):
        journal.core._verify_gateway_position_source_receipt(
            object(), scope=object(), snapshot={}, coverage=object(), facts=(),
            summary_counts={},
        )

    # The real Execution cancel bridge is initialized but has no source bound.
    cancel_port = binding[2][2][0]
    assert cancel_port._cancel_resolution_sources == {}
    with pytest.raises(RiskOrderJournalError, match="CANCEL_EXECUTION_SOURCE_BINDING_INVALID"):
        journal.core.bind_cancel_resolution_source(object(), object(), object(), object())


def test_facade_missing_ports_keeps_operations_unavailable(tmp_path):
    journal = RiskOrderJournal(tmp_path / "journal.sqlite3", writer_id="ports-missing")
    del journal.core._execution_composition_slot.bindings["ports"]
    with pytest.raises(RiskOrderJournalError, match="RISK_DISPATCH_EVIDENCE_PROVIDER_UNAVAILABLE"):
        journal.core._risk_dispatch_profile("ctp")
    with pytest.raises(RiskOrderJournalError, match="GROSS_BASELINE_POSITION_SOURCE_UNAVAILABLE"):
        journal.core._verify_gateway_position_source_receipt(object())
    with pytest.raises(RiskOrderJournalError, match="CANCEL_EXECUTION_SOURCE_UNAVAILABLE"):
        journal.core.bind_cancel_resolution_source()


def test_two_facades_never_share_ports(tmp_path):
    first = RiskOrderJournal(tmp_path / "first.sqlite3", writer_id="first")
    second = RiskOrderJournal(tmp_path / "second.sqlite3", writer_id="second")

    assert first.core is not second.core
    assert (
        first.core._execution_composition_slot.bindings["ports"]
        is not second.core._execution_composition_slot.bindings["ports"]
    )
    second.core._execution_composition_slot.bindings["ports"] = (
        first.core._execution_composition_slot.bindings["ports"]
    )
    with pytest.raises(RiskOrderJournalError, match="RISK_JOURNAL_PORT_OWNER_MISMATCH"):
        second.core._risk_dispatch_profile("ctp")
    assert first.core._risk_dispatch_profile("ctp").provider == "ctp"


def test_composition_read_adapter_uses_the_core_port(tmp_path):
    journal = RiskOrderJournal(tmp_path / "journal.sqlite3", writer_id="ports-reader")
    reader = object.__new__(_EvidenceReadContext)
    reader._core = journal.core
    assert reader._risk_dispatch_profile("ctp").provider == "ctp"

    bare = CoreRiskOrderJournal(tmp_path / "bare.sqlite3", writer_id="ports-bare")
    reader._core = bare
    with pytest.raises(RiskOrderJournalError, match="RISK_DISPATCH_EVIDENCE_PROVIDER_UNAVAILABLE"):
        reader._risk_dispatch_profile("ctp")


def test_cancel_admission_gate_handle_fails_closed_after_ports_removed(tmp_path):
    from decimal import Decimal

    from bt_api_base.service_protocol import WireScope
    from bt_api_execution import ExecutionScope, SqliteExecutionStore
    from bt_api_risk import AccountScope, DurableRiskGate, RiskPolicy

    from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate

    execution_scope = ExecutionScope("PAPER", "simulation", "ports-account", "ports-strategy")
    risk_scope = AccountScope("PAPER", "ports-account", "simulation")
    wire = WireScope(
        "ports-domain", account_ref="ports-account", strategy_ref="ports-strategy",
    )
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    journal = RiskOrderJournal(tmp_path / "journal.sqlite3", writer_id="cancel-gate")
    gate = DurableRiskGate(
        tmp_path / "risk.sqlite3",
        RiskPolicy("ports-policy", Decimal("0"), 0, permit_ttl_seconds=60.0,
                   notional_unit="USD"),
        execution_journal_authority=journal,
    )
    cancel_gate = RiskExecutionCancelAdmissionGate(
        gate, journal, store, risk_scope=risk_scope,
        execution_scope=execution_scope, wire_scope_template=wire,
    )
    assert cancel_gate._journal is journal
    assert journal.core.bind_cancel_resolution_source(
        risk_scope, execution_scope, wire, store,
    ) is None

    del journal.core._execution_composition_slot.bindings["ports"]
    with pytest.raises(RiskOrderJournalError, match="CANCEL_EXECUTION_SOURCE_UNAVAILABLE"):
        cancel_gate._journal.bind_cancel_resolution_source(
            risk_scope, execution_scope, wire, store,
        )
    with pytest.raises(RiskOrderJournalError, match="CANCEL_EXECUTION_SOURCE_UNAVAILABLE"):
        cancel_gate._journal._read_cancel_resolution_source(risk_scope)


def test_core_port_and_read_only_composition_bindings_coexist(tmp_path):
    from bt_api_py.service_execution_risk_composition import RiskExecutionEvidenceComposition

    journal = RiskOrderJournal(tmp_path / "journal.sqlite3", writer_id="ports-slot")
    composition = RiskExecutionEvidenceComposition(journal.core)
    assert journal.core._is_execution_composition_current(composition) is True
    assert "ports" in journal.core._execution_composition_slot.bindings
    assert journal.core._risk_dispatch_profile("ctp").provider == "ctp"


def test_ctp_bridge_obtains_a_core_fence_and_writes_a_command(tmp_path, monkeypatch):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        runtime_fixtures._resolve_resources(case)
        bridge = case.provider.last_runtime._owned_risk_bridge
        journal = bridge._journal
        fence = bridge._journal_fence

        # The bridge fenced the exact core through the facade, not a subclass.
        assert type(journal) is RiskOrderJournal
        assert type(journal.core) is CoreRiskOrderJournal
        assert fence.scope == bridge.account_scope
        assert journal.core._risk_dispatch_profile("ctp").provider == "ctp"

        command, created = journal.prepare_or_get(
            fence,
            principal_ref="port-test-principal",
            intent_id="port-test-intent",
            risk_intent_id="port-test-risk-intent",
            payload_sha256="a" * 64,
            canonical_payload={"schema": "", "instrument": "rb"},
            wire_scope={
                "execution_domain_ref": "port-test-domain",
                "account_ref": bridge.account_scope.account_id,
                "account_refs": [],
                "strategy_ref": "port-test-strategy",
                "run_id": None,
                "manual_book_ref": None,
                "portfolio_ref": None,
                "group_id": None,
                "leg_id": None,
            },
            exchange_name="SHFE",
            provider_account_id=bridge.account_scope.account_id,
            client_order_id="port-test-client-order",
            order_request={"instrument": "rb"},
            order_request_sha256="b" * 64,
        )
        assert created is True
        assert command.intent_id == "port-test-intent"
        stored = journal.get_command(
            fence.scope, "port-test-principal", "port-test-intent",
        )
        assert stored is not None
        assert stored.intent_id == "port-test-intent"
        assert stored.order_request_sha256 == "b" * 64
