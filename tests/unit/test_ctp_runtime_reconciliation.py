from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
import test_ctp_execution_runtime as runtime_fixtures
import test_ctp_runtime_order_chain as chain_fixtures
from bt_api_execution import ExecutionState
from bt_api_risk import DispatchResolutionReceiptStatus


def _outbox_high_water(case) -> int:
    page = case.resources.values["store"].read_execution_outbox_page(
        (case.execution_scope,), after_sequence=0, limit=64
    )
    assert page.has_more is False
    return page.cut.high_water_sequence


def _inject_settlement_audit_callback(case, runtime):
    """Deliver a late settlement query callback through the real SPI method."""

    api = case.factory.instances[0]
    request_ids = [
        call[1]
        for call in api.calls
        if call[0] == "ReqQrySettlementInfoConfirm"
    ]
    assert len(request_ids) == 1
    request_id = request_ids[0]
    trader = runtime._trader
    assert trader is not None and trader._spi is api.spi
    trader._spi.OnRspQrySettlementInfoConfirm(
        SimpleNamespace(
            BrokerID=runtime.config.trader.broker_id,
            InvestorID=runtime.config.trader.user_id,
            TradingDay=case.execution_scope.trading_day,
            ConfirmDate=case.execution_scope.trading_day,
        ),
        SimpleNamespace(ErrorID=0, ErrorMsg="synthetic late audit callback"),
        request_id,
        True,
    )
    return tuple(api.calls)


@pytest.mark.unit
def test_runtime_maintenance_reconciles_callback_progress_without_releasing_risk(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        component = runtime_fixtures._build_execution_component(case, monkeypatch)
        chain_fixtures._seed_synthetic_order_ref_cutover(case)
        reserve_errors = chain_fixtures._observe_authenticated_reserve(case, monkeypatch)
        chain_fixtures._observe_submit_authorized(case, monkeypatch)
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None

        api = case.factory.instances[0]
        intent_id = "ctp-runtime-maintenance-progress"
        callback_case, _session_binding, req_order_insert = (
            chain_fixtures._install_submit_callback(case, api, intent_id)
        )
        monkeypatch.setattr(api, "ReqOrderInsert", req_order_insert, raising=False)
        query_request_ids = chain_fixtures._install_zero_fill_submit_query_callback(
            case, api, monkeypatch, callback_case, intent_id
        )
        context = chain_fixtures._service_context(case)
        submitted = component.handlers["orders.submit"](
            context, chain_fixtures._order_arguments(intent_id)
        )
        assert reserve_errors == [], reserve_errors
        assert submitted["ok"] is True
        assert submitted["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert len(query_request_ids) == 1
        assert chain_fixtures._counts(api) == (1, 1, 0)

        reservation, command_id, command = chain_fixtures._submit_binding(case, intent_id)
        intent = resources.values["store"].get_intent(
            intent_id, scope=case.execution_scope
        )
        assert intent is not None
        submit_port = runtime._submit_port
        original_query = submit_port.query
        query_calls: list[str] = []

        def observed_query(candidate_intent):
            query_calls.append(candidate_intent.intent_id)
            return original_query(candidate_intent)

        monkeypatch.setattr(submit_port, "query", observed_query, raising=True)

        def service_state() -> str:
            response = component.handlers["orders.query"](
                context, {"intent_id": intent_id}
            )
            assert response["ok"] is True
            return response["result"]["record"]["state"]

        assert service_state() == ExecutionState.ACKED.value
        assert query_calls == []
        assert chain_fixtures._counts(api) == (1, 1, 0)
        bridge = resources.values["admission"]["order_gate"]
        risk_reserved_before = Decimal(
            case.risk_gate.snapshot(bridge.account_scope)["increase_notional"]
        )
        assert risk_reserved_before > 0

        # The first maintenance reads the same ACK through the callback-only
        # port. Re-reading unchanged callback evidence must not append another
        # Execution event or dispatch a second native order.
        runtime._next_reconciliation_ns = 0
        runtime.maintain(frozenset({case.instance_id}))
        assert query_calls == [intent_id]
        ack_high_water = _outbox_high_water(case)
        runtime._next_reconciliation_ns = 0
        runtime.maintain(frozenset({case.instance_id}))
        assert query_calls == [intent_id, intent_id]
        assert _outbox_high_water(case) == ack_high_water
        assert service_state() == ExecutionState.ACKED.value

        callback_fixtures = chain_fixtures.callback_fixtures
        callback_fixtures._emit_trade(
            callback_case,
            intent,
            reservation,
            trade_id="RUNTIME-MAINT-TRADE-001",
            sequence=1,
            volume=1,
        )
        callback_fixtures._emit_order_status(
            callback_case,
            intent,
            reservation,
            next(call[2] for call in api.calls if call[0] == "ReqOrderInsert"),
            status="1",
            submit_status="1",
            traded=1,
            sequence=2,
        )
        # Service query is a Store read; it cannot apply callback ingress.
        assert service_state() == ExecutionState.ACKED.value
        assert chain_fixtures._counts(api) == (1, 1, 0)

        runtime._next_reconciliation_ns = 0
        with pytest.raises(runtime_fixtures.CtpExecutionRuntimeError) as caught:
            runtime.maintain(frozenset({case.instance_id}))
        assert caught.value.code == "EXECUTION_RECONCILIATION_REVIEW_REQUIRED"
        assert query_calls[-1] == intent_id
        assert service_state() == ExecutionState.PARTIALLY_FILLED.value
        partial_record = resources.values["store"].get(
            intent_id, scope=case.execution_scope
        )
        assert partial_record.review_required is True
        assert runtime.status.ready is False
        assert runtime.status.phase == "TRACKED_FILL_REVIEW_PENDING"
        partial_high_water = _outbox_high_water(case)

        # A repeated callback snapshot is idempotent in the Execution outbox.
        runtime._next_reconciliation_ns = 0
        runtime.maintain(frozenset({case.instance_id}))
        assert service_state() == ExecutionState.PARTIALLY_FILLED.value
        assert _outbox_high_water(case) == partial_high_water
        assert chain_fixtures._counts(api) == (1, 1, 0)

        callback_fixtures._emit_trade(
            callback_case,
            intent,
            reservation,
            trade_id="RUNTIME-MAINT-TRADE-002",
            sequence=2,
            volume=1,
        )
        callback_fixtures._emit_order_status(
            callback_case,
            intent,
            reservation,
            next(call[2] for call in api.calls if call[0] == "ReqOrderInsert"),
            status="0",
            submit_status="3",
            traded=2,
            sequence=3,
        )
        assert service_state() == ExecutionState.PARTIALLY_FILLED.value
        runtime._next_reconciliation_ns = 0
        runtime.maintain(frozenset({case.instance_id}))
        assert service_state() == ExecutionState.FILLED.value
        assert chain_fixtures._counts(api) == (1, 1, 0)
        filled_record = resources.values["store"].get(
            intent_id, scope=case.execution_scope
        )
        assert filled_record.review_required is True
        assert runtime.status.ready is False

        trade_facts = resources.values["store"].read_ctp_dispatch_trade_facts(
            case.execution_scope, command_id
        )
        assert len(trade_facts.trades) == 2
        assert all(trade.cumulative_commission is None for trade in trade_facts.trades)
        assert all(trade.commission_quality == "INCOMPLETE" for trade in trade_facts.trades)
        risk_binding = bridge.read_dispatch_binding(
            chain_fixtures._PRINCIPAL, intent_id
        )
        assert risk_binding is not None
        assert risk_binding["submit_command_id"] == command.command_id
        risk_receipt = case.risk_gate.dispatch_resolution_receipt(
            risk_binding["permit_id"]
        )
        assert risk_receipt.status is (
            DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        )
        assert risk_receipt.dispatch_latch_active is False
        assert Decimal(
            case.risk_gate.snapshot(bridge.account_scope)["increase_notional"]
        ) == risk_reserved_before

        query_count = len(query_calls)
        component.close()
        case.component = None
        assert runtime.status.closed is True
        runtime.maintain(frozenset({case.instance_id}))
        assert len(query_calls) == query_count
        assert chain_fixtures._counts(api) == (1, 1, 0)


@pytest.mark.unit
def test_runtime_maintenance_fails_closed_when_facade_requires_review(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        component = runtime_fixtures._build_execution_component(case, monkeypatch)
        chain_fixtures._seed_synthetic_order_ref_cutover(case)
        reserve_errors = chain_fixtures._observe_authenticated_reserve(case, monkeypatch)
        chain_fixtures._observe_submit_authorized(case, monkeypatch)
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None

        api = case.factory.instances[0]
        intent_id = "ctp-runtime-maintenance-review-required"
        callback_case, _session_binding, req_order_insert = (
            chain_fixtures._install_submit_callback(case, api, intent_id)
        )
        monkeypatch.setattr(api, "ReqOrderInsert", req_order_insert, raising=False)
        chain_fixtures._install_zero_fill_submit_query_callback(
            case, api, monkeypatch, callback_case, intent_id
        )
        submitted = component.handlers["orders.submit"](
            chain_fixtures._service_context(case),
            chain_fixtures._order_arguments(intent_id),
        )
        assert reserve_errors == [], reserve_errors
        assert submitted["ok"] is True
        assert submitted["result"]["record"]["state"] == ExecutionState.ACKED.value

        store = resources.values["store"]
        intent = store.get_intent(intent_id, scope=case.execution_scope)
        assert intent is not None
        reservation, _command_id, _command = chain_fixtures._submit_binding(
            case, intent_id
        )
        callback_fixtures = chain_fixtures.callback_fixtures
        callback_fixtures._emit_trade(
            callback_case,
            intent,
            reservation,
            trade_id="RUNTIME-MAINT-REVIEW-TRADE-001",
            sequence=1,
            volume=1,
        )
        callback_fixtures._emit_order_status(
            callback_case,
            intent,
            reservation,
            next(call[2] for call in api.calls if call[0] == "ReqOrderInsert"),
            status="1",
            submit_status="1",
            traded=1,
            sequence=2,
        )

        bridge = resources.values["admission"]["order_gate"]
        hook_calls = []

        def fail_observation_hook(self, record, event, *, writer_lease):
            hook_calls.append((record.intent_id, event is not None))
            raise RuntimeError("synthetic reconciliation leaf failure")

        monkeypatch.setattr(
            type(bridge),
            "after_evidenced_observation",
            fail_observation_hook,
            raising=False,
        )
        before_high_water = _outbox_high_water(case)
        before_native_counts = chain_fixtures._counts(api)
        assert before_native_counts == (1, 1, 0)

        runtime._next_reconciliation_ns = 0
        with pytest.raises(runtime_fixtures.CtpExecutionRuntimeError) as caught:
            runtime.maintain(frozenset({case.instance_id}))

        assert caught.value.code == "TRACKED_FILL_REVIEW_CAPABILITY_INVALID"
        assert runtime.status.ready is False
        assert runtime.status.phase == "RECONCILIATION_FAILED"
        assert runtime.status.code == "TRACKED_FILL_REVIEW_CAPABILITY_INVALID"
        assert len(hook_calls) == 1
        assert hook_calls[0][0] == intent_id
        assert hook_calls[0][1] is True
        durable = store.get(intent_id, scope=case.execution_scope)
        assert durable is not None
        assert durable.state is ExecutionState.PARTIALLY_FILLED
        assert durable.review_required is True
        assert _outbox_high_water(case) > before_high_water
        assert chain_fixtures._counts(api) == before_native_counts

        component.close()
        case.component = None
        assert runtime.status.closed is True


@pytest.mark.unit
def test_runtime_maintenance_stops_before_callback_read_after_lease_takeover(
    tmp_path, monkeypatch
):
    base_scope = runtime_fixtures.ctp_owner_fixtures._scope()
    isolated_scope = replace(
        base_scope,
        account_ref=(
            "ctp-account-ref.v1:"
            + hashlib.sha256(b"ctp runtime lease takeover isolation").hexdigest()
        ),
    )
    monkeypatch.setattr(
        runtime_fixtures.ctp_owner_fixtures,
        "_scope",
        lambda: isolated_scope,
        raising=True,
    )
    clock = runtime_fixtures._install_lease_clock(monkeypatch)
    with runtime_fixtures._runtime_case(
        tmp_path, monkeypatch, lease_ttl_ns=9_000_000
    ) as case:
        assert case.execution_scope is isolated_scope
        assert case.config.execution_scope is isolated_scope
        assert case.service_scope.account_ref == isolated_scope.account_ref
        assert case.config.native_binding.account_ref == isolated_scope.account_ref
        component = runtime_fixtures._build_execution_component(case, monkeypatch)
        chain_fixtures._seed_synthetic_order_ref_cutover(case)
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None

        api = case.factory.instances[0]
        intent_id = "ctp-runtime-maintenance-fenced"
        callback_case, _session_binding, req_order_insert = (
            chain_fixtures._install_submit_callback(case, api, intent_id)
        )
        monkeypatch.setattr(api, "ReqOrderInsert", req_order_insert, raising=False)
        chain_fixtures._install_zero_fill_submit_query_callback(
            case, api, monkeypatch, callback_case, intent_id
        )
        submitted = component.handlers["orders.submit"](
            chain_fixtures._service_context(case),
            chain_fixtures._order_arguments(intent_id),
        )
        assert submitted["ok"] is True
        assert submitted["result"]["record"]["state"] == ExecutionState.ACKED.value

        submit_port = runtime._submit_port
        original_query = submit_port.query
        query_calls: list[str] = []

        def observed_query(candidate_intent):
            query_calls.append(candidate_intent.intent_id)
            return original_query(candidate_intent)

        monkeypatch.setattr(submit_port, "query", observed_query, raising=True)
        store = resources.values["store"]
        calls_before_audit = _inject_settlement_audit_callback(case, runtime)
        pending = store.read_ctp_callback_ingress_drain_status(
            runtime._callback_owner, writer_lease=runtime._writer_lease
        )
        assert pending.pending_count == 1
        assert pending.drained is False
        owner_type = type(runtime._execution_owner)
        original_pump = owner_type.pump_callbacks
        pump_calls = []

        def observed_pump(owner, max_events=32):
            pump_calls.append((owner, max_events))
            return original_pump(owner, max_events)

        monkeypatch.setattr(owner_type, "pump_callbacks", observed_pump)
        original_lease = runtime._writer_lease
        delay = resources.next_maintenance_delay()
        assert type(delay) is float and delay > 0
        clock.advance(int(delay * 1_000_000_000) + 1)
        assert store.release_lease(
            case.execution_scope,
            original_lease.owner_id,
            fencing_token=original_lease.fencing_token,
        ) is True
        takeover = store.acquire_or_renew_lease(
            case.execution_scope,
            "ctp-runtime-maintenance-takeover",
            ttl_ns=case.config.lease_ttl_ns,
            ctp_account_family_owner=runtime._family_owner,
        )
        case.retain_resources_on_cleanup = True
        assert takeover.fencing_token > original_lease.fencing_token

        with pytest.raises(runtime_fixtures.CtpExecutionRuntimeError) as caught:
            runtime.maintain(frozenset({case.instance_id}))
        assert caught.value.code == "WRITER_LEASE_RENEWAL_FAILED"
        assert runtime.status.ready is False
        assert runtime._writer_lease is original_lease
        assert query_calls == []
        assert pump_calls == []
        assert api.calls == list(calls_before_audit)
        assert chain_fixtures._counts(api) == (1, 1, 0)


@pytest.mark.unit
def test_runtime_maintenance_drains_audit_callback_without_active_orders(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        resources = runtime_fixtures._resolve_resources(case)
        runtime = case.provider.last_runtime
        assert runtime is not None and resources is not None
        store = resources.values["store"]
        api = case.factory.instances[0]

        empty_page = store.read_execution_outbox_page(
            (case.execution_scope,), after_sequence=0, limit=64
        )
        assert empty_page.intents == ()
        calls_before_audit = _inject_settlement_audit_callback(case, runtime)
        pending = store.read_ctp_callback_ingress_drain_status(
            runtime._callback_owner, writer_lease=runtime._writer_lease
        )
        assert pending.pending_count == 1
        assert pending.drained is False
        assert pending.last_source_sequence > pending.applied_source_sequence

        owner = runtime._execution_owner
        owner_type = type(owner)
        original_pump = owner_type.pump_callbacks
        pump_calls = []

        def observed_pump(candidate_owner, max_events=32):
            applied = original_pump(candidate_owner, max_events)
            pump_calls.append((candidate_owner, max_events, applied))
            return applied

        monkeypatch.setattr(owner_type, "pump_callbacks", observed_pump)
        runtime._next_reconciliation_ns = 0
        resources.maintain(frozenset({case.instance_id}))

        assert len(pump_calls) == 1
        assert pump_calls[0][0] is owner
        assert pump_calls[0][1] == case.config.callback_pump_events
        assert len(pump_calls[0][2]) == 1
        drained = store.read_ctp_callback_ingress_drain_status(
            runtime._callback_owner, writer_lease=runtime._writer_lease
        )
        assert drained.pending_count == 0
        assert drained.drained is True
        assert drained.applied_source_sequence == drained.last_source_sequence
        assert store.read_execution_outbox_page(
            (case.execution_scope,), after_sequence=0, limit=64
        ).intents == ()
        # The injected callback is the only setup-side SPI event; maintenance
        # itself issues no native order, cancel, or query request.
        assert api.calls == list(calls_before_audit)
        assert not any(
            call[0] in {"ReqOrderInsert", "ReqQryOrder", "ReqOrderAction"}
            for call in api.calls
        )
