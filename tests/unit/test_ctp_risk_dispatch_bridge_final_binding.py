"""Focused final checks for CTP/Risk action and dispatch binding."""

from dataclasses import replace

import pytest
import test_ctp_execution_runtime as runtime_fixtures
import test_ctp_runtime_order_chain as order_chain_fixtures
from bt_api_risk import IntentAction


@pytest.mark.unit
def test_open_action_mismatch_fails_before_prepare_reserve_or_native(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        component = runtime_fixtures._build_execution_component(case, monkeypatch)
        order_chain_fixtures._seed_synthetic_order_ref_cutover(case)
        reserve_errors = order_chain_fixtures._observe_authenticated_reserve(
            case, monkeypatch
        )
        bridge = case.resources.values["admission"]["order_gate"]
        adapter = bridge._adapter
        original_mapper = adapter._map_intent

        class OpeningOrderMappedAsReduce:
            wire_account_ref = original_mapper.wire_account_ref

            def __call__(self, intent):
                return replace(original_mapper(intent), action=IntentAction.REDUCE)

        monkeypatch.setattr(adapter, "_map_intent", OpeningOrderMappedAsReduce())
        risk_reserve_calls = []
        original_reserve = adapter.reserve

        def observe_reserve(intent):
            risk_reserve_calls.append(intent)
            return original_reserve(intent)

        monkeypatch.setattr(adapter, "reserve", observe_reserve)

        intent_id = "ctp-open-action-mismatch"
        reply = component.handlers["orders.submit"](
            order_chain_fixtures._service_context(case),
            order_chain_fixtures._order_arguments(intent_id),
        )

        assert reply["ok"] is True
        assert reply["code"] == "OK"
        record = reply["result"]["record"]
        assert record["state"] == "BLOCKED"
        assert record["unknown_reason"] == "admission_gate_error"
        assert record["dispatch_attempts"] == 0
        assert record["provider_order_id"] is None
        assert reserve_errors and reserve_errors[-1]["error_code"] == (
            "RISK_INTENT_ECONOMIC_MISMATCH"
        )
        assert case.journal.get_command(
            bridge.account_scope,
            order_chain_fixtures._PRINCIPAL,
            intent_id,
        ) is None
        assert risk_reserve_calls == []
        assert order_chain_fixtures._counts(case.factory.instances[0]) == (0, 0, 0)


@pytest.mark.unit
@pytest.mark.parametrize("readback_failure", ("missing", "mismatched"))
def test_dispatch_readback_failure_keeps_claimed_risk_and_blocks_retry(
    tmp_path, monkeypatch, readback_failure
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        component = runtime_fixtures._build_execution_component(case, monkeypatch)
        runtime = case.provider.last_runtime
        api = case.factory.instances[0]
        order_chain_fixtures._seed_synthetic_order_ref_cutover(case)
        native_calls = []

        def native_insert(field, request_id):
            native_calls.append((field, request_id))
            return 0

        monkeypatch.setattr(api, "ReqOrderInsert", native_insert, raising=False)

        bridge = runtime._owned_risk_bridge
        original_readback = bridge.read_dispatch_binding
        real_readbacks = []

        def failed_readback(principal_ref, intent_id):
            binding = original_readback(principal_ref, intent_id)
            real_readbacks.append(binding)
            if readback_failure == "missing":
                return None
            assert binding is not None
            mismatched = dict(binding)
            mismatched["risk_intent_id"] = "different-risk-intent"
            return mismatched

        monkeypatch.setattr(bridge, "read_dispatch_binding", failed_readback)

        dispatch_writes = []
        original_append = case.journal.append_event

        def observe_dispatch_write(*args, **kwargs):
            if kwargs.get("event_type") == "DISPATCHING":
                dispatch_writes.append(kwargs.get("payload"))
            return original_append(*args, **kwargs)

        monkeypatch.setattr(case.journal, "append_event", observe_dispatch_write)

        risk_claim_calls = []
        original_claim = case.risk_gate.claim_for_dispatch

        def observe_risk_claim(*args, **kwargs):
            risk_claim_calls.append(True)
            return original_claim(*args, **kwargs)

        monkeypatch.setattr(case.risk_gate, "claim_for_dispatch", observe_risk_claim)

        store_claim_calls = []
        original_store_claim = runtime._store.claim_ctp_dispatch_command_for_session

        def observe_store_claim(*args, **kwargs):
            store_claim_calls.append(True)
            return original_store_claim(*args, **kwargs)

        monkeypatch.setattr(
            runtime._store,
            "claim_ctp_dispatch_command_for_session",
            observe_store_claim,
        )

        intent_id = "ctp-dispatch-readback-" + readback_failure
        context = order_chain_fixtures._service_context(case)
        arguments = order_chain_fixtures._order_arguments(intent_id)
        component.handlers["orders.submit"](context, arguments)

        record = runtime._store.get(intent_id, scope=case.execution_scope)
        assert record is not None
        assert record.permit_reference is not None
        assert real_readbacks and real_readbacks[0] is not None
        assert len(dispatch_writes) == 1
        command_id = dispatch_writes[0]["ctp_dispatch_binding"]["submit_command_id"]
        assert command_id not in bridge._by_command
        assert risk_claim_calls == [True]
        assert store_claim_calls == []
        assert native_calls == []
        claim = case.risk_gate.dispatch_claim_binding(record.permit_reference)
        assert claim is not None
        assert claim.intent_id == intent_id
        freeze_reasons = case.risk_gate.active_freeze_reasons(bridge.account_scope)
        assert freeze_reasons
        assert case.risk_gate.snapshot(bridge.account_scope)["increase_count"] == 1
        uncertain_state = next(
            state
            for state in bridge._permits.values()
            if state.intent.intent_id == intent_id
        )
        assert uncertain_state.state == "DISPATCHING_UNCERTAIN"
        assert uncertain_state.dispatch_event is not None

        component.handlers["orders.submit"](context, arguments)

        assert len(real_readbacks) == 1
        assert len(dispatch_writes) == 1
        assert command_id not in bridge._by_command
        assert risk_claim_calls == [True]
        assert store_claim_calls == []
        assert native_calls == []
        assert case.risk_gate.dispatch_claim_binding(record.permit_reference) == claim
        assert case.risk_gate.active_freeze_reasons(bridge.account_scope) == freeze_reasons
