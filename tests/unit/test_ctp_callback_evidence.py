from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
import test_ctp_execution_owner as ctp_owner_fixtures
import test_ctp_managed_cancel_port as cancel_fixtures
import test_ctp_managed_submit_port as managed_submit_fixtures

from bt_api_base.service_protocol import WireScope
from bt_api_execution import (
    ContractValidationError,
    CtpDispatchCallbackApplyResult,
    CtpDispatchTradeFactApplyResult,
    ExecutionState,
    ManagedCancellationFacade,
    map_ctp_native_trade_fact_v2,
)
from bt_api_execution.ctp_callback_session import _flattened_fields, _source_session
from bt_api_execution.ctp_single_worker_candidate import stable_managed_command_id
from bt_api_py.ctp_callback_evidence import (
    CtpCallbackEvidenceError,
    CtpSessionIngressEvidenceVerifierV1,
    create_ctp_owner_callback_reconciliation_port,
)
from bt_api_py.ctp_managed_cancel_port import CtpManagedCancelPort
from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_risk import AccountScope, DurableRiskGate, RiskPolicy


def _install_evidence_port(case):
    port = create_ctp_owner_callback_reconciliation_port(
        case.store,
        case.scope,
        case.writer_lease,
        case.session_adapter,
        case.trader,
        session_generation_id=case.session_binding.session_generation_id,
    )
    verifier = port._callback_verifier
    assert type(verifier) is CtpSessionIngressEvidenceVerifierV1
    assert port._trade_fact_verifier is verifier
    case.reconciliation_port = port
    return port


def _emit_trade(case, intent, reservation, *, trade_id: str, sequence: int, volume: int):
    case.trader._spi.OnRtnTrade(
        SimpleNamespace(
            BrokerID="9999",
            InvestorID="investor-1",
            UserID="investor-1",
            InstrumentID=intent.instrument,
            OrderRef=reservation.order_ref,
            ExchangeID="SHFE",
            TradeID=trade_id,
            OrderSysID="SYS" + reservation.order_ref,
            TradingDay=case.scope.trading_day,
            Direction="0",
            OffsetFlag="0",
            HedgeFlag="1",
            Price=float(intent.price),
            Volume=volume,
            TradeDate=case.scope.trading_day,
            TradeTime="09:31:00",
            SequenceNo=sequence,
            BrokerOrderSeq=1,
            TradeSource="0",
        )
    )


def _emit_order_status(
    case, intent, reservation, request_id, *, status, submit_status, traded, sequence
):
    case.trader._spi.OnRtnOrder(
        SimpleNamespace(
            BrokerID="9999",
            InvestorID="investor-1",
            UserID="investor-1",
            InstrumentID=intent.instrument,
            RequestID=request_id,
            OrderRef=reservation.order_ref,
            ExchangeID="SHFE",
            OrderSysID="SYS" + reservation.order_ref,
            FrontID=case.session_binding.dispatch_front_id,
            SessionID=case.session_binding.dispatch_session_id,
            TradingDay=case.scope.trading_day,
            OrderStatus=status,
            OrderSubmitStatus=submit_status,
            VolumeTraded=traded,
            VolumeTotal=int(intent.quantity) - traded,
            VolumeTotalOriginal=int(intent.quantity),
            NotifySequence=sequence,
            SequenceNo=sequence,
            Direction="0",
            CombOffsetFlag="0",
            CombHedgeFlag="1",
            LimitPrice=float(intent.price),
            TimeCondition="3",
            VolumeCondition="1",
        )
    )


@contextmanager
def _real_cancel_stack(case, tmp_path, submit_port, submit_facade):
    risk_scope = AccountScope(
        case.scope.provider, case.scope.account_ref, case.scope.environment
    )
    wire_scope = WireScope(
        "ctp:callback-evidence-cancel-test",
        account_ref=case.scope.account_ref,
        strategy_ref=case.scope.strategy_id,
    )
    journal = RiskOrderJournal(
        tmp_path / "risk-order-journal.sqlite3",
        writer_id="ctp-callback-evidence-cancel-journal",
    )
    risk_gate = DurableRiskGate(
        tmp_path / "risk.sqlite3",
        RiskPolicy(
            "ctp-callback-evidence-cancel-v1",
            Decimal("0"),
            0,
            permit_ttl_seconds=60.0,
            notional_unit="USD",
        ),
        clock=lambda: 100.0,
        execution_journal_authority=journal,
    )
    try:
        admission = RiskExecutionCancelAdmissionGate(
            risk_gate,
            journal,
            case.store,
            risk_scope=risk_scope,
            execution_scope=case.scope,
            wire_scope_template=wire_scope,
        )
        facade = ManagedCancellationFacade(
            case.store,
            case.scope,
            acquire_writer_lease=submit_facade.acquire_writer_lease,
            admission_gate=admission,
        )
        port = CtpManagedCancelPort(
            submit_port._owner,
            admission,
            callback_pump_events=32,
            query_timeout=0.25,
        )
        yield SimpleNamespace(
            journal=journal,
            risk_gate=risk_gate,
            admission=admission,
            facade=facade,
            port=port,
        )
    finally:
        risk_gate.close()


@pytest.mark.unit
def test_live_trader_order_and_trade_ingress_is_verified_and_deduplicated(
    tmp_path, monkeypatch
):
    """Fake only the CTP API leaf; use the real callback sink, Store and verifier."""
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        reconciliation = _install_evidence_port(case)
        intent = replace(
            managed_submit_fixtures._day_intent(case, "verified-trades"),
            quantity=Decimal("3"),
        )
        submit_port, _gate, facade, context = managed_submit_fixtures._compose(
            case, intent
        )

        initial = facade.submit(intent, submit_port, admission_context=context)
        assert initial.state is ExecutionState.UNKNOWN
        assert initial.dispatch_attempts == 1
        reservation = case.store.read_ctp_order_identity(case.scope, intent.intent_id)
        assert reservation is not None
        calls = managed_submit_fixtures._submit_calls(case)
        assert len(calls) == 1
        request_id = calls[0][2]

        _emit_order_status(
            case,
            intent,
            reservation,
            request_id,
            status="3",
            submit_status="5",
            traded=0,
            sequence=1,
        )
        ack = reconciliation.apply_next_ingress()
        assert type(ack) is CtpDispatchCallbackApplyResult
        assert ack.projection_state == "ACKNOWLEDGED"
        observed = facade.reconcile_with_port(intent.intent_id, submit_port)
        assert observed.state is ExecutionState.ACKED
        assert observed.provider_order_id

        _emit_order_status(
            case,
            intent,
            reservation,
            request_id,
            status="3",
            submit_status="3",
            traded=0,
            sequence=2,
        )
        accepted = reconciliation.apply_next_ingress()
        assert type(accepted) is CtpDispatchCallbackApplyResult
        assert accepted.projection_state == "ACKNOWLEDGED"

        _emit_trade(
            case,
            intent,
            reservation,
            trade_id="CBTRD001",
            sequence=1,
            volume=1,
        )
        first_trade = reconciliation.apply_next_ingress()
        assert type(first_trade) is CtpDispatchTradeFactApplyResult
        assert first_trade.trade_quantity == 1
        assert first_trade.cumulative_trade_quantity == 1
        assert first_trade.duplicate is False

        _emit_order_status(
            case,
            intent,
            reservation,
            request_id,
            status="1",
            submit_status="1",
            traded=1,
            sequence=3,
        )
        partial = reconciliation.apply_next_ingress()
        assert type(partial) is CtpDispatchCallbackApplyResult
        assert partial.projection_state == "PARTIALLY_FILLED"
        partially_filled = facade.reconcile_with_port(intent.intent_id, submit_port)
        assert partially_filled.state is ExecutionState.PARTIALLY_FILLED
        assert partially_filled.filled_quantity == Decimal("1")

        _emit_trade(
            case,
            intent,
            reservation,
            trade_id="CBTRD001",
            sequence=1,
            volume=1,
        )
        duplicate = reconciliation.apply_next_ingress()
        assert type(duplicate) is CtpDispatchTradeFactApplyResult
        assert duplicate.duplicate is True
        assert duplicate.cumulative_trade_quantity == 1

        _emit_trade(
            case,
            intent,
            reservation,
            trade_id="CBTRD002",
            sequence=2,
            volume=2,
        )
        second_trade = reconciliation.apply_next_ingress()
        assert type(second_trade) is CtpDispatchTradeFactApplyResult
        assert second_trade.trade_quantity == 2
        assert second_trade.cumulative_trade_quantity == 3
        assert second_trade.duplicate is False

        _emit_order_status(
            case,
            intent,
            reservation,
            request_id,
            status="0",
            submit_status="3",
            traded=3,
            sequence=4,
        )
        filled = reconciliation.apply_next_ingress()
        assert type(filled) is CtpDispatchCallbackApplyResult
        assert filled.projection_state == "FILLED"
        final = facade.reconcile_with_port(intent.intent_id, submit_port)
        assert final.state is ExecutionState.FILLED
        assert final.filled_quantity == Decimal("3")
        assert len(managed_submit_fixtures._submit_calls(case)) == 1
        assert submit_port._queue.unfinished_tasks == 0


@pytest.mark.unit
def test_successful_order_insert_response_is_not_provider_order_ack(
    tmp_path, monkeypatch
):
    """Request acceptance is ledgered, but a separate order event proves ACK."""
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        reconciliation = _install_evidence_port(case)
        intent = managed_submit_fixtures._day_intent(case, "request-ack-is-not-order-ack")
        submit_port, _gate, facade, context = managed_submit_fixtures._compose(
            case, intent
        )
        pending = facade.submit(intent, submit_port, admission_context=context)
        assert pending.state is ExecutionState.UNKNOWN
        assert pending.dispatch_attempts == 1
        reservation = case.store.read_ctp_order_identity(case.scope, intent.intent_id)
        assert reservation is not None
        calls = managed_submit_fixtures._submit_calls(case)
        assert len(calls) == 1
        request_id = calls[0][2]
        command_id = stable_managed_command_id(
            "submit", intent.intent_id, reservation.runtime_order_id
        )

        case.trader._spi.OnRspOrderInsert(
            SimpleNamespace(
                BrokerID="9999",
                InvestorID="investor-1",
                UserID="investor-1",
                InstrumentID=intent.instrument,
                RequestID=request_id,
                OrderRef=reservation.order_ref,
                ExchangeID="SHFE",
            ),
            SimpleNamespace(ErrorID=0, ErrorMsg=""),
            request_id,
            True,
        )
        request_ack = reconciliation.apply_next_ingress()
        assert type(request_ack) is CtpDispatchCallbackApplyResult
        assert request_ack.projection_state == "ACKNOWLEDGED"
        assert request_ack.callback_key.callback_family == "ORDER"

        callback_row = case.store._connection.execute(
            """
            SELECT callback_family, projection_state
            FROM ctp_dispatch_callback_ledger
            WHERE account_key = ? AND command_id = ? AND callback_event_id = ?
            """,
            (
                case.scope.account_key,
                command_id,
                request_ack.callback_key.event_id,
            ),
        ).fetchone()
        assert callback_row is not None
        assert tuple(callback_row) == ("ORDER", "ACKNOWLEDGED")

        request_only_projection = case.store.read_ctp_dispatch_projection(
            case.scope, command_id
        )
        assert request_only_projection is not None
        assert request_only_projection.submit_action is not None
        assert request_only_projection.submit_action.order_state.provider_state is None
        still_unknown = facade.reconcile_with_port(intent.intent_id, submit_port)
        assert still_unknown.state is ExecutionState.UNKNOWN
        assert len(managed_submit_fixtures._submit_calls(case)) == 1

        _emit_order_status(
            case,
            intent,
            reservation,
            request_id,
            status="3",
            submit_status="3",
            traded=0,
            sequence=1,
        )
        order_ack = reconciliation.apply_next_ingress()
        assert type(order_ack) is CtpDispatchCallbackApplyResult
        assert order_ack.projection_state == "ACKNOWLEDGED"
        acknowledged = facade.reconcile_with_port(intent.intent_id, submit_port)
        assert acknowledged.state is ExecutionState.ACKED
        assert acknowledged.provider_order_id
        assert len(managed_submit_fixtures._submit_calls(case)) == 1


@pytest.mark.unit
@pytest.mark.parametrize("tamper", ["instrument", "bool_sequence"])
def test_candidate_callback_payload_tampering_is_rejected_against_store_event(
    tmp_path, monkeypatch, tamper
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        reconciliation = _install_evidence_port(case)
        intent = managed_submit_fixtures._day_intent(case, "candidate-tamper")
        submit_port, _gate, facade, context = managed_submit_fixtures._compose(
            case, intent
        )
        first = facade.submit(intent, submit_port, admission_context=context)
        assert first.state is ExecutionState.UNKNOWN
        reservation = case.store.read_ctp_order_identity(case.scope, intent.intent_id)
        assert reservation is not None
        request_id = managed_submit_fixtures._submit_calls(case)[0][2]
        managed_submit_fixtures._emit_order(
            case, intent, reservation, request_id, status="3", traded=0, sequence=1
        )

        verifier = reconciliation._callback_verifier
        original_verify = verifier.verify_callback
        rejected_codes = []

        def altered_candidate(command, callback, callback_payload, *, now_ns):
            altered = dict(callback_payload)
            altered_fields = dict(altered["native_fields"])
            if tamper == "instrument":
                altered_fields["InstrumentID"] = "not-" + altered_fields["InstrumentID"]
            else:
                assert type(altered_fields["SequenceNo"]) is int
                assert altered_fields["SequenceNo"] == 1
                altered_fields["SequenceNo"] = True
            altered["native_fields"] = altered_fields
            try:
                return original_verify(
                    command, callback, altered, now_ns=now_ns
                )
            except CtpCallbackEvidenceError as error:
                rejected_codes.append(error.code)
                raise

        monkeypatch.setattr(verifier, "verify_callback", altered_candidate)
        pending_event = case.store.read_next_ctp_callback_ingress(case.owner_handle)
        assert pending_event is not None
        assert pending_event.callback_name == "OnRtnOrder"
        with pytest.raises(ContractValidationError):
            reconciliation.apply_next_ingress()

        assert rejected_codes == ["CALLBACK_PAYLOAD_MISMATCH"]
        command_id = stable_managed_command_id(
            "submit", intent.intent_id, reservation.runtime_order_id
        )
        projection = case.store.read_ctp_dispatch_projection(case.scope, command_id)
        assert projection is not None
        assert projection.submit_action.order_state.provider_state is None
        owner_row = case.store._connection.execute(
            "SELECT owner_state, poison_code "
            "FROM ctp_dispatch_callback_session_owners WHERE owner_intent_id = ?",
            (case.owner_handle.owner_intent_id,),
        ).fetchone()
        assert owner_row is not None
        assert tuple(owner_row) == ("POISONED", "callback_apply_failure")
        application_row = case.store._connection.execute(
            "SELECT outcome FROM ctp_dispatch_callback_ingress_applications "
            "WHERE owner_intent_id = ? AND source_sequence = ?",
            (case.owner_handle.owner_intent_id, pending_event.source_sequence),
        ).fetchone()
        assert application_row is None
        assert len(managed_submit_fixtures._submit_calls(case)) == 1


@pytest.mark.unit
def test_trade_verifier_requires_exact_pending_event_and_current_trader_source(
    tmp_path, monkeypatch
):
    """A real event is one-use evidence bound to its exact Trader source."""
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        reconciliation = _install_evidence_port(case)
        intent = managed_submit_fixtures._day_intent(case, "trade-event-identity")
        submit_port, _gate, facade, context = managed_submit_fixtures._compose(
            case, intent
        )
        initial = facade.submit(intent, submit_port, admission_context=context)
        assert initial.state is ExecutionState.UNKNOWN
        reservation = case.store.read_ctp_order_identity(case.scope, intent.intent_id)
        assert reservation is not None
        request_id = managed_submit_fixtures._submit_calls(case)[0][2]
        managed_submit_fixtures._emit_order(
            case, intent, reservation, request_id, status="3", traded=0, sequence=1
        )
        assert reconciliation.apply_next_ingress().projection_state == "ACKNOWLEDGED"
        ack = facade.reconcile_with_port(intent.intent_id, submit_port)
        assert ack.state is ExecutionState.ACKED

        _emit_trade(
            case,
            intent,
            reservation,
            trade_id="IDENTITY1",
            sequence=1,
            volume=1,
        )
        event = case.store.read_next_ctp_callback_ingress(case.owner_handle)
        raw = json.loads(event.record_payload_json)
        native_field = _flattened_fields(raw, 0)
        native_session, _binding = _source_session(case.session_adapter)
        trade_fact = map_ctp_native_trade_fact_v2(native_session, native_field)
        command_id = stable_managed_command_id(
            "submit", intent.intent_id, reservation.runtime_order_id
        )
        command = case.store.read_ctp_dispatch_command(case.scope, command_id)
        assert command is not None and command.operation == "SUBMIT"
        verifier = reconciliation._trade_fact_verifier

        copied_event = replace(event)
        assert copied_event == event
        assert copied_event is not event
        with pytest.raises(CtpCallbackEvidenceError) as copied_error:
            verifier.verify_trade_fact(
                command, trade_fact, copied_event, now_ns=time.time_ns()
            )
        assert copied_error.value.code == "TRADE_SOURCE_MISMATCH"
        assert case.store.read_next_ctp_callback_ingress(case.owner_handle) is event

        original_source = case.trader._session_native_api
        case.trader._session_native_api = object()
        try:
            with pytest.raises(CtpCallbackEvidenceError) as source_error:
                verifier.verify_trade_fact(
                    command, trade_fact, event, now_ns=time.time_ns()
                )
        finally:
            case.trader._session_native_api = original_source
        assert source_error.value.code == "CALLBACK_OWNER_UNAVAILABLE"
        assert case.store.read_next_ctp_callback_ingress(case.owner_handle) is event

        applied = reconciliation.apply_next_ingress()
        assert type(applied) is CtpDispatchTradeFactApplyResult
        assert applied.trade_quantity == 1
        with pytest.raises(CtpCallbackEvidenceError) as stale_error:
            verifier.verify_trade_fact(
                command, trade_fact, event, now_ns=time.time_ns()
            )
        assert stale_error.value.code == "CALLBACK_EVENT_UNAVAILABLE"
        assert len(managed_submit_fixtures._submit_calls(case)) == 1
        assert submit_port._queue.unfinished_tasks == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error_id", "expected_state", "expected_projection"),
    [
        (0, ExecutionState.ACKED, "ACKNOWLEDGED"),
        (17, ExecutionState.REJECTED, "REJECTED"),
    ],
)
def test_live_order_action_response_maps_to_verified_cancel_observation(
    tmp_path, monkeypatch, error_id, expected_state, expected_projection
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        reconciliation = _install_evidence_port(case)
        target, reservation, submit_port, submit_facade = cancel_fixtures._open_target(
            case, f"action-{error_id}", monkeypatch
        )
        with _real_cancel_stack(case, tmp_path, submit_port, submit_facade) as stack:
            cancel_intent = cancel_fixtures._cancel_intent(
                target, reservation, f"action-{error_id}"
            )
            pending = stack.facade.cancel(
                cancel_intent,
                stack.port,
                before_dispatch=stack.port.prepare_target,
            )
            assert pending.state is ExecutionState.UNKNOWN
            assert pending.dispatch_attempts == 1
            calls = cancel_fixtures._cancel_calls(case)
            assert len(calls) == 1
            assert len(cancel_fixtures._query_calls(case)) == 1

            action_field, request_id = cancel_fixtures._emit_cancel_response(
                case, error_id=error_id
            )
            assert action_field.OrderRef == reservation.order_ref
            assert action_field.OrderSysID == "SYS" + reservation.order_ref
            assert request_id > 0
            applied = reconciliation.apply_next_ingress()
            assert type(applied) is CtpDispatchCallbackApplyResult
            assert applied.projection_state == expected_projection

            observation = stack.port.reconcile(cancel_intent)
            assert observation is not None
            assert observation.cancel_id == cancel_intent.cancel_id
            assert observation.target_intent_id == target.intent_id
            assert observation.provider_order_id == cancel_intent.provider_order_id
            assert observation.state is expected_state
            if error_id:
                assert observation.reason_code == "ctp_cancel_rejected"

            resolved = stack.facade.reconcile(observation)
            assert resolved.state is expected_state
            assert resolved.dispatch_attempts == 1
            assert len(cancel_fixtures._cancel_calls(case)) == 1
            assert stack.port._queue.unfinished_tasks == 0
