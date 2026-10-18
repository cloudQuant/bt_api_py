from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import test_ctp_execution_owner as ctp_owner_fixtures
import test_ctp_managed_submit_port as managed_submit_fixtures

from bt_api_execution import (
    CtpCancelTarget,
    CtpOrderTargetProjectionHandle,
    ContractValidationError,
    IntentConflictError,
    ExecutionState,
)
from bt_api_py.ctp_order_target_query import CtpOrderTargetQueryEvaluator


def _native_order(
    case,
    *,
    intent=None,
    reservation=None,
    order_status: str = "3",
    exchange_id: str = "SHFE",
):
    intent = intent or case.intent
    reservation = reservation or case.reservation
    return SimpleNamespace(
        BrokerID="9999",
        InvestorID="investor-1",
        UserID="investor-1",
        InstrumentID=intent.instrument,
        OrderRef=reservation.order_ref,
        ExchangeID=exchange_id,
        OrderSysID="SYS" + reservation.order_ref,
        FrontID=case.session_binding.dispatch_front_id,
        SessionID=case.session_binding.dispatch_session_id,
        TradingDay=case.scope.trading_day,
        OrderStatus=order_status,
        OrderSubmitStatus="3",
        RequestID=41,
        NotifySequence=7,
        SequenceNo=7,
        VolumeTraded=0,
        VolumeTotal=int(intent.quantity),
        VolumeTotalOriginal=int(intent.quantity),
        Direction="0",
        CombOffsetFlag="0",
        CombHedgeFlag="1",
        LimitPrice=float(intent.price),
    )


def _install_native_query_rows(case, monkeypatch, rows):
    response_info = SimpleNamespace(ErrorID=0, ErrorMsg="")

    def req_qry_order(field, request_id):
        case.api.calls.append(("ReqQryOrder", field, request_id))
        for row in rows:
            case.api.spi.OnRspQryOrder(row, response_info, request_id, False)
        case.api.spi.OnRspQryOrder(None, response_info, request_id, True)
        return 0

    monkeypatch.setattr(case.api, "ReqQryOrder", req_qry_order, raising=False)


def _stage_cancel_target(
    case,
    handle,
    *,
    command_id: str,
    native_request_id: int = 2_000_000_000,
):
    """Exercise projection consumption only; this does not authorize dispatch."""

    projection = handle.projection
    target = CtpCancelTarget(
        order_ref=projection.order_ref,
        exchange_id=projection.exchange_id,
        order_sys_id=projection.order_sys_id,
        front_id=projection.front_id,
        session_id=projection.session_id,
    )
    return case.store.stage_ctp_dispatch_command(
        case.scope,
        command_id,
        "CANCEL",
        {
            "InstrumentID": projection.instrument_id,
            "OrderRef": target.order_ref,
            "ExchangeID": target.exchange_id,
            "OrderSysID": target.order_sys_id,
            "FrontID": target.front_id,
            "SessionID": target.session_id,
            "ActionFlag": "0",
        },
        approval_use_id="query-stage-approval-" + command_id,
        approval_digest=hashlib.sha256(command_id.encode("ascii")).hexdigest(),
        session_binding=ctp_owner_fixtures._session_binding_payload(
            case.session_binding
        ),
        writer_lease=case.writer_lease,
        cancel_target=target,
        managed_action_id="query-stage-action-" + command_id,
        session_generation_id=case.session_binding.session_generation_id,
        dispatch_front_id=case.session_binding.dispatch_front_id,
        dispatch_session_id=case.session_binding.dispatch_session_id,
        native_request_id=native_request_id,
        cancel_target_projection=handle,
    )


def _projection_consumption_count(case, handle) -> int:
    row = case.store._connection.execute(
        "SELECT COUNT(*) FROM ctp_order_target_projection_consumptions "
        "WHERE projection_id = ?",
        (handle.projection_id,),
    ).fetchone()
    return int(row[0])


def _query_audit_rows(case):
    return case.store._connection.execute(
        """
        SELECT i.source_sequence, i.callback_name, i.callback_class, a.outcome,
               i.record_payload_json
        FROM ctp_dispatch_callback_ingress AS i
        LEFT JOIN ctp_dispatch_callback_ingress_applications AS a
          ON a.owner_intent_id = i.owner_intent_id
         AND a.source_sequence = i.source_sequence
        WHERE i.owner_intent_id = ? AND i.callback_name = 'OnRspQryOrder'
        ORDER BY i.source_sequence
        """,
        (case.owner_handle.owner_intent_id,),
    ).fetchall()


def _dispatch_day_submit(case, suffix: str):
    """Create the exact durable SUBMIT required by the query evaluator."""

    intent = managed_submit_fixtures._day_intent(case, suffix)
    port, _gate, facade, admission_context = managed_submit_fixtures._compose(
        case, intent
    )
    execution = facade.submit(
        intent, port, admission_context=admission_context
    )
    assert execution.state is ExecutionState.UNKNOWN
    assert execution.review_required is True
    assert execution.dispatch_attempts == 1
    assert port._queue.unfinished_tasks == 0

    reservation = case.store.read_ctp_order_identity(case.scope, intent.intent_id)
    assert reservation is not None
    command_id = managed_submit_fixtures.stable_managed_command_id(
        "submit", intent.intent_id, reservation.runtime_order_id
    )
    command = case.store.read_ctp_dispatch_command(case.scope, command_id)
    assert command is not None
    assert command.operation == "SUBMIT"
    assert command.status == "COMPLETED"
    assert command.local_queue_receipt_queued is True
    assert command.order_ref == reservation.order_ref
    assert len(managed_submit_fixtures._submit_calls(case)) == 1
    return intent, reservation, port


@pytest.mark.unit
def test_real_query_ingress_readback_mints_and_consumes_one_same_store_cancel_target(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation, submit_port = _dispatch_day_submit(
            case, "query-target-positive"
        )
        row = _native_order(case, intent=intent, reservation=reservation)
        _install_native_query_rows(case, monkeypatch, (row,))

        evaluator = CtpOrderTargetQueryEvaluator(submit_port._owner)
        handle = evaluator.query_and_issue(
            intent.intent_id,
            instrument_id=intent.instrument,
            exchange_id="SHFE",
            order_sys_id=row.OrderSysID,
            timeout=0.25,
        )

        assert type(handle) is CtpOrderTargetProjectionHandle
        projection = handle.projection
        assert case.store.read_ctp_order_target_projection(case.scope, handle) == projection
        assert (
            projection.account_key,
            projection.scope_key,
            projection.trading_day,
            projection.managed_intent_id,
            projection.runtime_order_id,
            projection.order_ref,
        ) == (
            case.scope.account_key,
            case.scope.key,
            case.scope.trading_day,
            intent.intent_id,
            reservation.runtime_order_id,
            reservation.order_ref,
        )
        assert (
            projection.instrument_id,
            projection.exchange_id,
            projection.order_sys_id,
            projection.front_id,
            projection.session_id,
        ) == (
            intent.instrument,
            "SHFE",
            row.OrderSysID,
            case.session_binding.dispatch_front_id,
            case.session_binding.dispatch_session_id,
        )
        assert (
            projection.provider_state,
            projection.quantity,
            projection.traded_quantity,
            projection.remaining_quantity,
        ) == ("OPEN", int(intent.quantity), 0, int(intent.quantity))
        assert (
            projection.query_record_count,
            projection.query_match_count,
            projection.query_complete,
            projection.query_terminal,
            projection.query_timed_out,
            projection.late_callback_count,
        ) == (1, 1, True, True, False, 0)
        assert projection.query_error_id in (None, 0)
        assert projection.session_generation_id == case.session_binding.session_generation_id
        assert projection.connection_generation == case.session_binding.connection_generation
        assert projection.query_front_id == case.session_binding.dispatch_front_id
        assert projection.query_session_id == case.session_binding.dispatch_session_id
        assert projection.query_request_id > 0
        provenance = projection.query_provenance
        assert provenance is not None
        assert provenance.fence_id
        assert provenance.owner_intent_id == case.owner_handle.owner_intent_id
        assert provenance.account_key == case.scope.account_key
        assert provenance.scope_key == case.scope.key
        assert provenance.trading_day == case.scope.trading_day
        assert provenance.managed_intent_id == intent.intent_id
        assert provenance.runtime_order_id == reservation.runtime_order_id
        assert provenance.order_ref == reservation.order_ref
        assert provenance.session_binding_sha256 == case.session_binding.session_binding_sha256
        assert provenance.session_generation_id == case.session_binding.session_generation_id
        assert provenance.connection_generation == case.session_binding.connection_generation
        assert provenance.dispatch_front_id == case.session_binding.dispatch_front_id
        assert provenance.dispatch_session_id == case.session_binding.dispatch_session_id
        assert provenance.writer_owner_id == case.writer_lease.owner_id
        assert provenance.writer_fencing_token == case.writer_lease.fencing_token
        assert provenance.query_request_id == projection.query_request_id
        assert provenance.end_source_sequence > provenance.start_source_sequence
        assert provenance.applied_source_sequence == provenance.end_source_sequence
        assert provenance.source_event_count == provenance.query_event_count == 2
        assert len(provenance.window_sha256) == 64
        int(provenance.window_sha256, 16)
        assert provenance.issued_at_ns > 0
        assert provenance.expires_at_ns > provenance.issued_at_ns
        for digest in (
            projection.account_fingerprint_sha256,
            projection.registration_digest,
            projection.query_filters_sha256,
            projection.query_records_sha256,
            projection.source_evidence_sha256,
        ):
            assert len(digest) == 64
            int(digest, 16)

        native_queries = [call for call in case.api.calls if call[0] == "ReqQryOrder"]
        assert len(native_queries) == 1
        _name, native_filter, native_request_id = native_queries[0]
        assert native_request_id == projection.query_request_id
        assert row.RequestID == 41
        assert row.RequestID != native_request_id
        assert {
            name: getattr(native_filter, name)
            for name in ("BrokerID", "InvestorID", "InstrumentID", "ExchangeID", "OrderSysID")
        } == {
            "BrokerID": "9999",
            "InvestorID": "investor-1",
            "InstrumentID": intent.instrument,
            "ExchangeID": "SHFE",
            "OrderSysID": row.OrderSysID,
        }

        audit_rows = _query_audit_rows(case)
        assert len(audit_rows) == 2
        assert all(
            callback_name == "OnRspQryOrder"
            and callback_class == "AUDIT_QUERY"
            and outcome == "AUDIT_ONLY"
            for _sequence, callback_name, callback_class, outcome, _payload in audit_rows
        )
        query_callback_payloads = [
            json.loads(record_payload_json)
            for _sequence, _name, _class, _outcome, record_payload_json in audit_rows
        ]
        assert [
            payload["named_args"][2]["value"] for payload in query_callback_payloads
        ] == [native_request_id, native_request_id]
        assert [
            payload["named_args"][3]["value"] for payload in query_callback_payloads
        ] == [False, True]
        owner_row = case.store._connection.execute(
            "SELECT economic_query_observed FROM ctp_dispatch_callback_session_owners "
            "WHERE owner_intent_id = ?",
            (case.owner_handle.owner_intent_id,),
        ).fetchone()
        assert owner_row[0] == 1
        assert case.store.read_next_ctp_callback_ingress(case.owner_handle) is None

        staged = _stage_cancel_target(
            case, handle, command_id="query-target-cancel-command-1"
        )
        assert staged.status == "READY"
        assert staged.operation == "CANCEL"
        assert staged.cancel_target_order_ref == reservation.order_ref
        assert _projection_consumption_count(case, handle) == 1

        with pytest.raises(
            IntentConflictError,
            match="^CTP target projection is already bound to another action$",
        ):
            _stage_cancel_target(
                case,
                handle,
                command_id="query-target-cancel-command-replay",
                native_request_id=2_000_000_001,
            )
        assert (
            case.store.read_ctp_dispatch_command(
                case.scope, "query-target-cancel-command-replay"
            )
            is None
        )
        assert _projection_consumption_count(case, handle) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("row_status", "row_exchange", "duplicate", "wrong_sys_id"),
    (
        ("3", "DCE", False, False),
        ("0", "SHFE", False, False),
        ("3", "SHFE", True, False),
        ("3", "SHFE", False, True),
    ),
    ids=("wrong-exchange", "terminal-order", "ambiguous-match", "wrong-order-sys-id"),
)
def test_real_query_requires_one_current_open_matching_native_row(
    tmp_path, monkeypatch, row_status, row_exchange, duplicate, wrong_sys_id
):
    scenario_dir = tmp_path / "query-negative"
    scenario_dir.mkdir()
    with ctp_owner_fixtures._active_case(scenario_dir, monkeypatch) as case:
        intent, reservation, submit_port = _dispatch_day_submit(
            case, "query-target-negative"
        )
        row = _native_order(
            case,
            intent=intent,
            reservation=reservation,
            order_status=row_status,
            exchange_id=row_exchange,
        )
        if wrong_sys_id:
            row.OrderSysID = "SYS-OTHER"
        rows = (row, row) if duplicate else (row,)
        _install_native_query_rows(case, monkeypatch, rows)

        evaluator = CtpOrderTargetQueryEvaluator(submit_port._owner)
        with pytest.raises(ContractValidationError):
            evaluator.query_and_issue(
                intent.intent_id,
                instrument_id=intent.instrument,
                exchange_id="SHFE",
                order_sys_id="SYS" + reservation.order_ref,
                timeout=0.25,
            )

        if wrong_sys_id:
            native_queries = [call for call in case.api.calls if call[0] == "ReqQryOrder"]
            assert len(native_queries) == 1
            _name, native_filter, _request_id = native_queries[0]
            assert native_filter.OrderSysID == "SYS" + reservation.order_ref
            assert row.OrderSysID != native_filter.OrderSysID

        assert case.store._connection.execute(
            "SELECT COUNT(*) FROM ctp_order_target_projections "
            "WHERE managed_intent_id = ?",
            (intent.intent_id,),
        ).fetchone()[0] == 0
        owner_row = case.store._connection.execute(
            "SELECT economic_query_observed FROM ctp_dispatch_callback_session_owners "
            "WHERE owner_intent_id = ?",
            (case.owner_handle.owner_intent_id,),
        ).fetchone()
        assert owner_row[0] == 1
        audit_rows = _query_audit_rows(case)
        assert len(audit_rows) == (3 if duplicate else 2)
        assert all(
            callback_name == "OnRspQryOrder"
            and callback_class == "AUDIT_QUERY"
            and outcome == "AUDIT_ONLY"
            for _sequence, callback_name, callback_class, outcome, _payload in audit_rows
        )
        assert case.store.read_next_ctp_callback_ingress(case.owner_handle) is None


@pytest.mark.unit
def test_real_query_rejects_equal_result_without_live_query_provenance(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation, submit_port = _dispatch_day_submit(
            case, "query-target-unproven-readback"
        )
        row = _native_order(case, intent=intent, reservation=reservation)
        _install_native_query_rows(case, monkeypatch, (row,))
        real_get_query_result = case.trader.get_query_result
        readback_results = []
        readback_request_ids = []

        def tracked_current_readback(request_id):
            readback_request_ids.append(request_id)
            live_result = real_get_query_result(request_id)
            assert live_result is not None
            assert live_result.query_source is not None
            if len(readback_results) == 0:
                unproven_result = replace(live_result)
                assert unproven_result == live_result
                assert unproven_result.query_source is None
                readback_results.append((live_result, unproven_result))
                return unproven_result
            return live_result

        monkeypatch.setattr(case.trader, "get_query_result", tracked_current_readback)

        evaluator = CtpOrderTargetQueryEvaluator(submit_port._owner)
        with pytest.raises(ContractValidationError):
            evaluator.query_and_issue(
                intent.intent_id,
                instrument_id=intent.instrument,
                exchange_id="SHFE",
                order_sys_id=row.OrderSysID,
                timeout=0.25,
            )

        assert len(readback_results) == 1
        live_result, unproven_result = readback_results[0]
        assert live_result.request_id > 0
        assert unproven_result.request_id == live_result.request_id
        assert readback_request_ids
        assert set(readback_request_ids) == {live_result.request_id}
        assert [call[0] for call in case.api.calls].count("ReqQryOrder") == 1
        submit_port._owner.pump_callbacks()
        assert case.store._connection.execute(
            "SELECT COUNT(*) FROM ctp_order_target_projections "
            "WHERE managed_intent_id = ?",
            (intent.intent_id,),
        ).fetchone()[0] == 0
        owner_row = case.store._connection.execute(
            "SELECT economic_query_observed FROM ctp_dispatch_callback_session_owners "
            "WHERE owner_intent_id = ?",
            (case.owner_handle.owner_intent_id,),
        ).fetchone()
        assert owner_row[0] == 1
        audit_rows = _query_audit_rows(case)
        assert len(audit_rows) == 2
        assert all(
            callback_name == "OnRspQryOrder"
            and callback_class == "AUDIT_QUERY"
            and outcome == "AUDIT_ONLY"
            for _sequence, callback_name, callback_class, outcome, _payload in audit_rows
        )
        assert case.store.read_next_ctp_callback_ingress(case.owner_handle) is None


@pytest.mark.unit
def test_real_query_rejects_callback_arriving_after_native_terminal(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation, submit_port = _dispatch_day_submit(
            case, "query-target-late-callback"
        )
        row = _native_order(case, intent=intent, reservation=reservation)
        _install_native_query_rows(case, monkeypatch, (row,))
        real_query = case.trader.query_orders_result
        real_get_query_result = case.trader.get_query_result
        completed_queries = []
        readback_request_ids = []
        late_callback_request_ids = []
        late_response_info = SimpleNamespace(ErrorID=0, ErrorMsg="")
        late_row = _native_order(case, intent=intent, reservation=reservation)

        def tracked_completed_query(*args, **kwargs):
            result = real_query(*args, **kwargs)
            completed_queries.append(result)
            return result

        def inject_late_callback_then_readback(request_id):
            readback_request_ids.append(request_id)
            if not late_callback_request_ids:
                assert completed_queries
                assert completed_queries[0].request_id == request_id
                case.api.spi.OnRspQryOrder(
                    late_row, late_response_info, request_id, False
                )
                late_callback_request_ids.append(request_id)
            return real_get_query_result(request_id)

        monkeypatch.setattr(case.trader, "query_orders_result", tracked_completed_query)
        monkeypatch.setattr(
            case.trader, "get_query_result", inject_late_callback_then_readback
        )

        evaluator = CtpOrderTargetQueryEvaluator(submit_port._owner)
        with pytest.raises(ContractValidationError):
            evaluator.query_and_issue(
                intent.intent_id,
                instrument_id=intent.instrument,
                exchange_id="SHFE",
                order_sys_id=row.OrderSysID,
                timeout=0.25,
            )

        assert len(completed_queries) == 1
        query_request_id = completed_queries[0].request_id
        assert query_request_id > 0
        assert late_callback_request_ids == [query_request_id]
        assert readback_request_ids
        assert set(readback_request_ids) == {query_request_id}
        current_result = real_get_query_result(query_request_id)
        assert current_result is not None
        assert current_result.complete is True
        assert current_result.late_callback_count == 1
        submit_port._owner.pump_callbacks()
        assert case.store._connection.execute(
            "SELECT COUNT(*) FROM ctp_order_target_projections "
            "WHERE managed_intent_id = ?",
            (intent.intent_id,),
        ).fetchone()[0] == 0
        owner_row = case.store._connection.execute(
            "SELECT economic_query_observed FROM ctp_dispatch_callback_session_owners "
            "WHERE owner_intent_id = ?",
            (case.owner_handle.owner_intent_id,),
        ).fetchone()
        assert owner_row[0] == 1
        audit_rows = _query_audit_rows(case)
        assert len(audit_rows) == 3
        assert all(
            callback_name == "OnRspQryOrder"
            and callback_class == "AUDIT_QUERY"
            and outcome == "AUDIT_ONLY"
            for _sequence, callback_name, callback_class, outcome, _payload in audit_rows
        )
        payloads = [
            json.loads(record_payload_json)
            for _sequence, _name, _class, _outcome, record_payload_json in audit_rows
        ]
        assert payloads[-1]["named_args"][2]["value"] == query_request_id
        assert payloads[-1]["named_args"][3]["value"] is False
        assert case.store.read_next_ctp_callback_ingress(case.owner_handle) is None
