from __future__ import annotations

from dataclasses import replace

import pytest
import test_ctp_callback_evidence as callback_fixtures
import test_ctp_execution_owner as owner_fixtures
import test_ctp_managed_submit_port as submit_fixtures
import test_ctp_order_target_query as query_fixtures
from bt_api_execution import ContractValidationError, ExecutionState

import bt_api_py.ctp_terminal_order_query as terminal_query_module
from bt_api_py.ctp_terminal_order_query import (
    CtpTerminalOrderQueryEvidenceV1,
    CtpTerminalOrderQueryNotReadyV1,
    CtpTerminalOrderQueryReader,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("terminal_state", "submit_status"),
    (("CANCELLED", "3"), ("REJECTED", "4")),
    ids=("cancelled-zero-fill", "rejected-zero-fill"),
)
def test_terminal_order_query_binds_exact_callback_query_window_and_readback(
    tmp_path, monkeypatch, terminal_state, submit_status
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        reconciliation = callback_fixtures._install_evidence_port(case)
        intent, reservation, submit_port = query_fixtures._dispatch_day_submit(
            case, "terminal-" + terminal_state.lower()
        )
        submit_call = submit_fixtures._submit_calls(case)[0]
        native_request_id = submit_call[2]
        order_status = "5" if terminal_state == "CANCELLED" else "4"
        callback_fixtures._emit_order_status(
            case,
            intent,
            reservation,
            native_request_id,
            status=order_status,
            submit_status=submit_status,
            traded=0,
            sequence=1,
        )
        applied = reconciliation.apply_next_ingress()
        assert applied.projection_state == terminal_state

        row = query_fixtures._native_order(
            case,
            intent=intent,
            reservation=reservation,
            order_status=order_status,
            exchange_id="SHFE",
        )
        row.OrderSubmitStatus = submit_status
        row.RequestID = native_request_id
        query_fixtures._install_native_query_rows(case, monkeypatch, (row,))

        reader = CtpTerminalOrderQueryReader(submit_port._owner)
        evidence = reader.query_terminal_order(intent.intent_id, timeout=0.25)
        assert type(evidence) is CtpTerminalOrderQueryEvidenceV1
        assert reader.readback(evidence) is evidence
        assert (
            evidence.terminal_state,
            evidence.account_key,
            evidence.scope_key,
            evidence.trading_day,
            evidence.managed_intent_id,
            evidence.runtime_order_id,
            evidence.order_ref,
            evidence.request_id,
            evidence.front_id,
            evidence.session_id,
            evidence.quantity,
            evidence.volume_traded,
            evidence.filled_quantity,
            evidence.trade_fact_count,
            evidence.trade_notional,
            evidence.commission_quality,
        ) == (
            terminal_state,
            case.scope.account_key,
            case.scope.key,
            case.scope.trading_day,
            intent.intent_id,
            reservation.runtime_order_id,
            reservation.order_ref,
            native_request_id,
            case.session_binding.dispatch_front_id,
            case.session_binding.dispatch_session_id,
            int(intent.quantity),
            0,
            0,
            0,
            "0",
            "INCOMPLETE",
        )
        assert evidence.query_start_source_sequence == evidence.order_source_sequence
        assert evidence.query_applied_source_sequence == evidence.query_end_source_sequence
        assert len(evidence.query_callback_ingress_digests) == 2
        assert len([call for call in case.api.calls if call[0] == "ReqQryOrder"]) == 1
        assert len(submit_fixtures._submit_calls(case)) == 1


@pytest.mark.unit
def test_terminal_order_query_does_not_query_nonterminal_or_issue_forged_evidence(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        reconciliation = callback_fixtures._install_evidence_port(case)
        intent, reservation, submit_port = query_fixtures._dispatch_day_submit(
            case, "terminal-not-ready"
        )
        submit_call = submit_fixtures._submit_calls(case)[0]
        native_request_id = submit_call[2]
        callback_fixtures._emit_order_status(
            case,
            intent,
            reservation,
            native_request_id,
            status="3",
            submit_status="3",
            traded=0,
            sequence=1,
        )
        assert reconciliation.apply_next_ingress().projection_state == "ACKNOWLEDGED"

        reader = CtpTerminalOrderQueryReader(submit_port._owner)
        not_ready = reader.query_terminal_order(intent.intent_id, timeout=0.25)
        assert type(not_ready) is CtpTerminalOrderQueryNotReadyV1
        assert not_ready.reason_code == "ORDER_NOT_TERMINAL"
        assert [call for call in case.api.calls if call[0] == "ReqQryOrder"] == []

        row = query_fixtures._native_order(
            case, intent=intent, reservation=reservation, order_status="5"
        )
        row.RequestID = native_request_id
        query_fixtures._install_native_query_rows(case, monkeypatch, (row,))
        callback_fixtures._emit_order_status(
            case,
            intent,
            reservation,
            native_request_id,
            status="5",
            submit_status="3",
            traded=0,
            sequence=2,
        )
        assert reconciliation.apply_next_ingress().projection_state == "CANCELLED"
        evidence = reader.query_terminal_order(intent.intent_id, timeout=0.25)
        assert type(evidence) is CtpTerminalOrderQueryEvidenceV1

        copied = replace(evidence)
        assert copied == evidence and copied is not evidence
        with pytest.raises(ContractValidationError):
            reader.readback(copied)
        assert reader.readback(evidence) is evidence


@pytest.mark.unit
def test_terminal_order_query_readback_rechecks_expiry_after_store_validation(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        reconciliation = callback_fixtures._install_evidence_port(case)
        intent, reservation, submit_port = query_fixtures._dispatch_day_submit(
            case, "terminal-readback-expiry"
        )
        native_request_id = submit_fixtures._submit_calls(case)[0][2]
        row = query_fixtures._native_order(
            case, intent=intent, reservation=reservation, order_status="5"
        )
        row.RequestID = native_request_id
        query_fixtures._install_native_query_rows(case, monkeypatch, (row,))
        callback_fixtures._emit_order_status(
            case,
            intent,
            reservation,
            native_request_id,
            status="3",
            submit_status="3",
            traded=0,
            sequence=1,
        )
        assert reconciliation.apply_next_ingress().projection_state == "ACKNOWLEDGED"
        callback_fixtures._emit_order_status(
            case,
            intent,
            reservation,
            native_request_id,
            status="5",
            submit_status="3",
            traded=0,
            sequence=2,
        )
        assert reconciliation.apply_next_ingress().projection_state == "CANCELLED"

        reader = CtpTerminalOrderQueryReader(submit_port._owner)
        evidence = reader.query_terminal_order(intent.intent_id, timeout=0.25)
        assert type(evidence) is CtpTerminalOrderQueryEvidenceV1

        clock = {"advanced": False, "checks": 0}

        def monotonic_ns():
            clock["checks"] += 1
            if clock["advanced"]:
                return evidence.expires_at_ns
            return evidence.expires_at_ns - 1

        store_snapshot = case.store.read_ctp_owner_callback_observation_snapshot
        store_reads = {"count": 0}

        def read_snapshot_then_advance(*args, **kwargs):
            result = store_snapshot(*args, **kwargs)
            store_reads["count"] += 1
            clock["advanced"] = True
            return result

        monkeypatch.setattr(terminal_query_module, "_monotonic_ns", monotonic_ns)
        monkeypatch.setattr(
            case.store,
            "read_ctp_owner_callback_observation_snapshot",
            read_snapshot_then_advance,
        )
        with pytest.raises(ContractValidationError, match="expired during readback"):
            reader.readback(evidence)

        assert store_reads["count"] == 1
        assert clock["checks"] == 2


@pytest.mark.unit
def test_terminal_order_query_rejects_filled_cancelled_target_before_query(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        reconciliation = callback_fixtures._install_evidence_port(case)
        intent = replace(
            submit_fixtures._day_intent(case, "terminal-cancelled-with-fill"),
            quantity=case.intent.quantity * 2,
        )
        submit_port, _gate, facade, admission_context = submit_fixtures._compose(
            case, intent
        )
        execution = facade.submit(
            intent, submit_port, admission_context=admission_context
        )
        assert execution.state is ExecutionState.UNKNOWN
        assert execution.review_required is True
        assert execution.dispatch_attempts == 1
        assert submit_port._queue.unfinished_tasks == 0
        reservation = case.store.read_ctp_order_identity(case.scope, intent.intent_id)
        assert reservation is not None
        assert int(intent.quantity) == 2
        assert len(submit_fixtures._submit_calls(case)) == 1
        native_request_id = submit_fixtures._submit_calls(case)[0][2]
        callback_fixtures._emit_order_status(
            case,
            intent,
            reservation,
            native_request_id,
            status="3",
            submit_status="3",
            traded=0,
            sequence=1,
        )
        assert reconciliation.apply_next_ingress().projection_state == "ACKNOWLEDGED"
        callback_fixtures._emit_trade(
            case,
            intent,
            reservation,
            trade_id="TERMINALFILL1",
            sequence=1,
            volume=1,
        )
        trade = reconciliation.apply_next_ingress()
        assert trade.trade_quantity == 1
        assert trade.cumulative_trade_quantity == 1
        callback_fixtures._emit_order_status(
            case,
            intent,
            reservation,
            native_request_id,
            status="5",
            submit_status="3",
            traded=1,
            sequence=2,
        )
        assert reconciliation.apply_next_ingress().projection_state == "CANCELLED"

        reader = CtpTerminalOrderQueryReader(submit_port._owner)
        not_ready = reader.query_terminal_order(intent.intent_id, timeout=0.25)
        assert type(not_ready) is CtpTerminalOrderQueryNotReadyV1
        assert not_ready.reason_code == "ORDER_HAS_FILL"
        assert [call for call in case.api.calls if call[0] == "ReqQryOrder"] == []
        assert len(submit_fixtures._submit_calls(case)) == 1

