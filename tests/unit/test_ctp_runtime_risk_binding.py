"""Runtime binding checks using the real CTP composition and synthetic native leaf."""

from dataclasses import replace

import pytest
import test_ctp_execution_runtime as runtime_fixtures
import test_ctp_runtime_order_chain as chain_fixtures
from bt_api_execution import ExecutionState
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    IntentAction,
    RiskIntent,
)

from bt_api_py.ctp_execution_runtime import (
    CtpExecutionResourceProvider,
    CtpExecutionRuntimeError,
)
from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate
from bt_api_py.service_components import ServiceResourceConstructionError


@pytest.mark.unit
def test_runtime_rejects_plain_risk_adapter_before_write_readiness(tmp_path, monkeypatch):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        def plain_factory(context):
            risk_scope = AccountScope(
                context.execution_scope.provider,
                context.execution_scope.account_ref,
                context.execution_scope.environment,
            )

            def map_intent(intent):
                return RiskIntent(
                    intent_id=intent.intent_id,
                    scope=risk_scope,
                    action=IntentAction.INCREASE,
                    notional=intent.quantity * intent.price,
                    payload_fingerprint=intent.fingerprint,
                    strategy_id=intent.scope.strategy_id,
                    instrument=intent.instrument,
                    quantity=intent.quantity,
                    notional_unit="USD",
                )

            return {
                "order_gate": DurableRiskAdmissionAdapter(case.risk_gate, map_intent),
                "cancel_gate": RiskExecutionCancelAdmissionGate(
                    case.risk_gate, case.journal, context.store,
                    risk_scope=risk_scope,
                    execution_scope=context.execution_scope,
                    wire_scope_template=case.service_scope,
                ),
            }

        case.config = replace(case.config, risk_admission_factory=plain_factory)
        case.provider = CtpExecutionResourceProvider(case.config)
        with pytest.raises(ServiceResourceConstructionError):
            runtime_fixtures._resolve_resources(case, allow_construction_failure=True)
        runtime = case.provider.last_runtime
        assert runtime.status.ready is False
        assert runtime.status.code == "RISK_DISPATCH_BRIDGE_REQUIRED"
        assert case.resources.ready() is False
        assert runtime._owned_risk_bridge is None
        assert all(
            call[0] not in {"ReqOrderInsert", "ReqOrderAction"}
            for api in case.factory.instances for call in api.calls
        )
        case.resources.close()
        assert runtime.last_stop_receipt.complete is True
        assert runtime.status.closed is True


@pytest.mark.unit
def test_runtime_closes_journal_fence_after_native_stop_and_callback_drain(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        runtime_fixtures._build_execution_component(case, monkeypatch)
        runtime = case.provider.last_runtime
        bridge = runtime._owned_risk_bridge
        assert bridge is case.resources.values["admission"]["order_gate"]
        risk_scope = AccountScope(
            case.execution_scope.provider,
            case.execution_scope.account_ref,
            case.execution_scope.environment,
        )
        with case.journal.writer_fence(risk_scope) as fence:
            original_generation = fence.fencing_token
        real_close = bridge.close
        calls = []

        def observed_close():
            assert runtime.last_stop_receipt.complete is True
            assert runtime._store_closed is False
            drain = runtime._store.read_ctp_callback_ingress_drain_status(
                runtime._callback_owner, writer_lease=runtime._writer_lease
            )
            assert drain.drained is True
            calls.append(drain)
            real_close()

        monkeypatch.setattr(bridge, "close", observed_close)
        case.resources.close()
        assert runtime.status.closed is True
        assert runtime._risk_bridge_closed is True
        assert len(calls) == 1
        case.resources.close()
        assert len(calls) == 1
        with case.journal.writer_fence(risk_scope) as fresh:
            assert fresh.fencing_token > original_generation


@pytest.mark.unit
def test_runtime_retains_store_when_journal_fence_close_fails(tmp_path, monkeypatch):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        runtime_fixtures._build_execution_component(case, monkeypatch)
        runtime = case.provider.last_runtime
        bridge = runtime._owned_risk_bridge
        real_close = bridge.close
        attempts = []

        def close_once_failed():
            attempts.append(True)
            if len(attempts) == 1:
                raise RuntimeError("synthetic journal fence close failure")
            real_close()

        monkeypatch.setattr(bridge, "close", close_once_failed)
        with pytest.raises(CtpExecutionRuntimeError) as caught:
            case.resources.close()
        assert caught.value.code == "RISK_BRIDGE_CLOSE_UNCERTAIN"
        assert runtime.status.closed is False
        assert runtime._risk_bridge_closed is False
        assert runtime._store_closed is False
        assert runtime.last_stop_receipt.complete is True
        drain = runtime._store.read_ctp_callback_ingress_drain_status(
            runtime._callback_owner, writer_lease=runtime._writer_lease
        )
        assert drain.drained is True
        native_calls = tuple(tuple(api.calls) for api in case.factory.instances)
        case.resources.close()
        assert len(attempts) == 2
        assert runtime.status.closed is True
        assert runtime._risk_bridge_closed is True
        assert tuple(tuple(api.calls) for api in case.factory.instances) == native_calls


@pytest.mark.unit
@pytest.mark.parametrize("failure_stage", ["claim_append", "claim_readback"])
def test_postclaim_journal_failure_keeps_latch_and_prevents_native_retry(
    tmp_path, monkeypatch, failure_stage
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        component = runtime_fixtures._build_execution_component(case, monkeypatch)
        runtime = case.provider.last_runtime
        api = case.factory.instances[0]
        chain_fixtures._seed_synthetic_order_ref_cutover(case)
        native_calls = []

        def native_insert(field, request_id):
            native_calls.append((field, request_id))
            return 0

        monkeypatch.setattr(api, "ReqOrderInsert", native_insert, raising=False)
        claim_calls = []
        real_claim = case.risk_gate.claim_for_dispatch

        def counted_claim(*args, **kwargs):
            claim_calls.append(True)
            return real_claim(*args, **kwargs)

        monkeypatch.setattr(case.risk_gate, "claim_for_dispatch", counted_claim)
        injected = []
        if failure_stage == "claim_append":
            real_append = case.journal.append_event

            def append_failed(*args, **kwargs):
                if kwargs.get("event_type") == "CLAIMED":
                    injected.append(True)
                    raise RuntimeError("synthetic postclaim journal write failure")
                return real_append(*args, **kwargs)

            monkeypatch.setattr(case.journal, "append_event", append_failed)
        else:
            real_read = case.journal.get_event

            def changed_claim_readback(*args, **kwargs):
                event = real_read(*args, **kwargs)
                if event is not None and event.event_type == "CLAIMED":
                    injected.append(True)
                    return replace(event, record_sha256="0" * 64)
                return event

            monkeypatch.setattr(case.journal, "get_event", changed_claim_readback)

        intent_id = "runtime-journal-failure-" + failure_stage
        context = chain_fixtures._service_context(case)
        args = chain_fixtures._order_arguments(intent_id)
        reserve_errors = chain_fixtures._observe_authenticated_reserve(case, monkeypatch)
        component.handlers["orders.submit"](context, args)
        assert not reserve_errors, reserve_errors
        record = runtime._store.get(intent_id, scope=case.execution_scope)
        assert record is not None
        assert record.state is (
            ExecutionState.BLOCKED if failure_stage == "claim_append"
            else ExecutionState.UNKNOWN
        )
        assert record.review_required is True
        assert record.permit_reference is not None
        assert len(injected) == 1
        assert len(claim_calls) == 1
        assert native_calls == []
        claim = case.risk_gate.dispatch_claim_binding(record.permit_reference)
        assert claim.intent_id == intent_id
        risk_scope = runtime._owned_risk_bridge.account_scope
        freeze_reasons = case.risk_gate.active_freeze_reasons(risk_scope)
        assert freeze_reasons
        assert case.risk_gate.snapshot(risk_scope)["increase_count"] == 1

        component.handlers["orders.submit"](context, args)
        assert len(claim_calls) == 1
        assert len(injected) == 1
        assert native_calls == []
        assert case.risk_gate.dispatch_claim_binding(record.permit_reference) == claim
        assert case.risk_gate.active_freeze_reasons(risk_scope) == freeze_reasons
        journal_command = case.journal.get_command(
            runtime._owned_risk_bridge.account_scope,
            context.principal_ref,
            intent_id,
        )
        assert journal_command.state == (
            "RESERVED" if failure_stage == "claim_append" else "CLAIMED"
        )
