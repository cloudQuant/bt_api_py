"""Generic GTC cancellation replay with real Risk; provider leaves are synthetic."""

from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import time

import pytest
import test_service_execution_risk_cancel_claim as claim_fixtures
import test_service_execution_risk_cancel_terminal_history as terminal_fixtures
import test_service_risk_order_exposure_integration as risk_fixtures
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    CancelIntent, CancelObservation, ExecutionScope, ExecutionState,
    ManagedCancellationFacade, ManagedExecutionFacade, OrderIntent, OrderType,
    PositionEffect, Side, SqliteExecutionStore, TimeInForce,
)
from bt_api_execution.strategy_cancel import owner_cancel_batch_key, owner_cancel_id
from bt_api_py.bt_api import BtApi
from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate
from bt_api_py.service_risk_order_entry import RiskOrderEntryService, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_risk import (
    DispatchResolutionReceiptStatus, DurableRiskAdmissionAdapter, DurableRiskGate,
    InstrumentRiskAdmissionMapper, InstrumentRiskMetadata, InstrumentRiskRegistry,
    RiskPolicy,
)


class _SyntheticCancel:
    def __init__(self):
        self.dispatches = []
        self.readbacks = []
        self.observation = None

    def cancel(self, intent):
        self.dispatches.append(intent.cancel_id)
        # An absent immediate acknowledgement remains durably UNKNOWN.
        return None

    def reconcile_cancel(self, intent):
        self.readbacks.append(intent.cancel_id)
        return self.observation


@contextmanager
def _stack(tmp_path, monkeypatch):
    risk_scope = risk_fixtures._ACCOUNT_SCOPE
    scope = ExecutionScope(
        risk_scope.provider, risk_scope.environment, risk_scope.account_id,
        "late-cancel-strategy",
    )
    run_id = "late-cancel-run"
    principal = "late-cancel-strategy-principal"
    owner_principal = "late-cancel-owner-principal"
    owner_actor = "late-cancel-owner-actor"
    command_id = "late-cancel-command"
    domain = "late-cancel-domain"
    wire = WireScope(domain, account_ref=scope.account_ref,
                     strategy_ref=scope.strategy_id, run_id=run_id)
    template = WireScope(domain, account_ref=scope.account_ref,
                         strategy_ref=scope.strategy_id)
    wire_payload = {
        "execution_domain_ref": wire.execution_domain_ref,
        "account_ref": wire.account_ref, "account_refs": list(wire.account_refs),
        "strategy_ref": wire.strategy_ref, "run_id": wire.run_id,
        "manual_book_ref": wire.manual_book_ref, "portfolio_ref": wire.portfolio_ref,
        "group_id": wire.group_id, "leg_id": wire.leg_id,
    }
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    journal = RiskOrderJournal(tmp_path / "journal.sqlite3", writer_id="late-cancel-journal")
    gate = DurableRiskGate(
        tmp_path / "risk.sqlite3",
        RiskPolicy("late-cancel-policy-v1", Decimal("1000"),
                   max_increase_count=5, permit_ttl_seconds=60, notional_unit="USD"),
        execution_journal_authority=journal,
    )
    # This fixture installs its synthetic feed directly; plugin discovery is outside its scope.
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    api = BtApi(debug=False)
    try:
        now_ns = time.time_ns()
        registry = InstrumentRiskRegistry((InstrumentRiskMetadata(
            instrument=risk_fixtures._V2_INSTRUMENT,
            metadata_version=risk_fixtures._V2_METADATA_VERSION,
            as_of_ns=now_ns - 1_000_000_000,
            expires_at_ns=now_ns + 3_600_000_000_000,
            tick_size=Decimal("0.01"), quantity_step=Decimal("0.001"),
            contract_multiplier=Decimal("1"), max_gross_notional=Decimal("1000"),
            taker_fee_bps=Decimal("0"), fixed_fee=Decimal("0"),
            max_slippage_bps=Decimal("0"), quantity_unit="BTC", valuation_unit="USD",
        ),))
        feed = risk_fixtures._V2SyntheticBtApiFeed(
            str(tmp_path / "provider.sqlite3"), ("open",)
        )
        api.exchange_feeds[risk_fixtures._EXCHANGE] = feed
        target = RiskOrderTarget(
            risk_scope, scope.strategy_id, risk_fixtures._EXCHANGE,
            risk_scope.account_id, registry,
        )
        service = RiskOrderEntryService(
            api, {template: target},
            {template: DurableRiskAdmissionAdapter(
                gate, InstrumentRiskAdmissionMapper(risk_scope, registry)
            )},
            journal, gate, {template: risk_fixtures._contract(("GTC",))},
        )
        submit = terminal_fixtures._RiskBackedDispatch(
            service, WireIdentity(WireRole.STRATEGY_CLIENT, principal, scope.strategy_id),
            wire,
        )
        # The outer facade delegates admission to the actual Risk service above.
        orders = ManagedExecutionFacade(store, scope, writer_id="late-cancel-writer",
                                        allow_unprotected=True)
        target_intent = OrderIntent(
            intent_id="late-cancel-target", scope=scope, signal_id="late-cancel-signal",
            instrument=risk_fixtures._V2_INSTRUMENT, side=Side.BUY,
            position_effect=PositionEffect.OPEN, order_type=OrderType.LIMIT,
            quantity=Decimal("1"), price=Decimal("20"), time_in_force=TimeInForce.GTC,
            metadata_version=risk_fixtures._V2_METADATA_VERSION,
        )
        target_record = orders.submit(
            target_intent, submit, owner_strategy_ref=scope.strategy_id,
            owner_run_id=run_id, owner_wire_scope=wire_payload,
        )
        assert target_record.state is ExecutionState.ACKED, submit.submit_reply
        assert submit.submitted == [target_intent.intent_id]
        target_command = journal.get_command(risk_scope, principal, target_intent.intent_id)
        assert target_command is not None
        target_receipt = gate.dispatch_resolution_receipt(target_command.permit_id)
        assert target_receipt.status is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        assert target_receipt.dispatch_latch_active is False
        assert target_command.canonical_payload["time_in_force"] == "GTC"
        with journal.writer_fence(risk_scope) as fence:
            assert journal.has_resolved_ack_history(
                fence, principal_ref=principal, intent_id=target_intent.intent_id,
                receipt=target_receipt,
            )
        batch = store.freeze_owner_cancel_batch(
            scope=scope, strategy_ref=scope.strategy_id, run_id=run_id,
            wire_scope=wire_payload, command_id=command_id,
            principal_ref=owner_principal, actor_ref=owner_actor, delegate_ref=None,
            writer_lease=orders.acquire_writer_lease(),
        )
        items = batch["body"]["items"]
        cancel_id = owner_cancel_id(command_id=command_id, intent_id=target_intent.intent_id)
        assert len(items) == 1
        assert items[0]["decision"] == "READY"
        assert items[0]["cancel_id"] == cancel_id
        assert items[0]["provider_order_id"] == target_record.provider_order_id
        assert items[0]["payload_sha256"] == target_intent.fingerprint
        cancel = CancelIntent(
            cancel_id, scope, target_intent.intent_id, target_record.provider_order_id,
            target_intent.metadata_version,
            tags={
                "owner_cancel_binding_version": "1",
                "owner_cancel_batch_key": owner_cancel_batch_key(
                    scope_key=scope.key, wire_scope=wire_payload, command_id=command_id),
                "owner_cancel_command_id": command_id,
                "owner_cancel_principal_ref": owner_principal,
                "owner_cancel_actor_ref": owner_actor,
                "owner_cancel_strategy_ref": scope.strategy_id,
                "owner_cancel_run_id": run_id,
            },
        )
        cancel_gate = RiskExecutionCancelAdmissionGate(
            gate, journal, store, risk_scope=risk_scope,
            execution_scope=scope, wire_scope_template=template,
        )
        facade = ManagedCancellationFacade(
            store, scope, acquire_writer_lease=orders.acquire_writer_lease,
            admission_gate=cancel_gate,
        )
        yield SimpleNamespace(
            facade=facade, intent=cancel, dispatcher=_SyntheticCancel(), gate=gate,
            risk_path=tmp_path / "risk.sqlite3", store=store, scope=scope,
            target_command=target_command, target_receipt=target_receipt, feed=feed,
        )
    finally:
        try:
            api.close()
        finally:
            gate.close()
            store.close()


def _start_unknown(stack):
    pending = stack.facade.cancel(stack.intent, stack.dispatcher)
    assert pending.state is ExecutionState.UNKNOWN
    assert pending.dispatch_attempts == 1
    receipt = stack.gate.cancel_dispatch_resolution_receipt(pending.permit_reference)
    assert receipt.status.value == "UNRESOLVED_ACTIVE"
    assert receipt.dispatch_latch_active is True
    assert stack.dispatcher.dispatches == [stack.intent.cancel_id]
    return pending


def _assert_terminal(stack, pending, state):
    resolved = stack.facade.cancel(stack.intent, stack.dispatcher)
    assert resolved.state is state
    assert resolved.dispatch_attempts == 1
    receipt = stack.gate.cancel_dispatch_resolution_receipt(pending.permit_reference)
    assert receipt.status.value == "TERMINAL_RESOLVED"
    assert receipt.dispatch_latch_active is False
    assert receipt.proof is not None and receipt.proof.terminal_state.value == state.value
    assert stack.gate.dispatch_resolution_receipt(
        stack.target_command.permit_id
    ) == stack.target_receipt
    before = (claim_fixtures._risk_state(stack), tuple(stack.dispatcher.readbacks),
              tuple(stack.dispatcher.dispatches), stack.feed.snapshot())
    assert stack.facade.cancel(stack.intent, stack.dispatcher) == resolved
    assert (claim_fixtures._risk_state(stack), tuple(stack.dispatcher.readbacks),
            tuple(stack.dispatcher.dispatches), stack.feed.snapshot()) == before
    assert stack.dispatcher.dispatches == [stack.intent.cancel_id]


@pytest.mark.unit
@pytest.mark.parametrize("persist_ack", (False, True))
def test_terminal_replay_resolves_original_risk_claim_once(tmp_path, persist_ack, monkeypatch):
    with _stack(tmp_path, monkeypatch) as stack:
        pending = _start_unknown(stack)
        intent = stack.intent
        ack = CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id)
        if persist_ack:
            assert stack.facade.reconcile(ack).state is ExecutionState.ACKED
        else:
            before = claim_fixtures._risk_state(stack)
            stack.dispatcher.observation = ack
            assert stack.facade.cancel(intent, stack.dispatcher) == pending
            assert claim_fixtures._risk_state(stack) == before
        active = stack.gate.cancel_dispatch_resolution_receipt(pending.permit_reference)
        assert active.status.value == "UNRESOLVED_ACTIVE" and active.dispatch_latch_active
        stack.dispatcher.observation = CancelObservation.cancelled(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id)
        _assert_terminal(stack, pending, ExecutionState.CANCELLED)


@pytest.mark.unit
def test_rejected_replay_resolves_original_risk_claim_once(tmp_path, monkeypatch):
    with _stack(tmp_path, monkeypatch) as stack:
        pending = _start_unknown(stack)
        intent = stack.intent
        stack.dispatcher.observation = CancelObservation.rejected(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id,
            "synthetic_cancel_rejected")
        _assert_terminal(stack, pending, ExecutionState.REJECTED)


@pytest.mark.unit
def test_unavailable_nonterminal_or_unbound_replay_keeps_risk_latched(tmp_path, monkeypatch):
    with _stack(tmp_path, monkeypatch) as stack:
        pending = _start_unknown(stack)
        intent = stack.intent
        before = claim_fixtures._risk_state(stack)
        receipt = stack.gate.cancel_dispatch_resolution_receipt(pending.permit_reference)
        valid = CancelObservation.cancelled(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id)
        observations = [None, CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id)]
        for field in ("cancel_id", "target_intent_id", "provider_order_id"):
            observations.append(replace(valid, **{field: getattr(valid, field) + "-other"}))
        for observation in observations:
            stack.dispatcher.observation = observation
            assert stack.facade.cancel(intent, stack.dispatcher) == pending
            assert claim_fixtures._risk_state(stack) == before

        def failed_readback(_intent):
            raise RuntimeError("synthetic local readback failure")

        monkeypatch.setattr(stack.dispatcher, "reconcile_cancel", failed_readback)
        assert stack.facade.cancel(intent, stack.dispatcher) == pending

        class LegacyDispatcher:
            def cancel(self, _intent):
                raise AssertionError("replay must not redispatch")

        assert stack.facade.cancel(intent, LegacyDispatcher()) == pending
        assert stack.store.get_cancel(intent.cancel_id, scope=stack.scope) == pending
        assert claim_fixtures._risk_state(stack) == before
        assert stack.gate.cancel_dispatch_resolution_receipt(pending.permit_reference) == receipt
        assert stack.dispatcher.dispatches == [intent.cancel_id]
