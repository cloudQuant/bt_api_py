from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
import test_ctp_callback_evidence as callback_fixtures
import test_ctp_execution_owner as owner_fixtures
import test_ctp_order_target_query as query_fixtures

import bt_api_py.ctp_order_target_query as target_query_module
import bt_api_py.ctp_zero_fill_tracked_order as zero_fill_module
from bt_api_execution import (
    ContractValidationError,
    CtpOwnerCallbackObservationNotReadyV1,
    ExecutionState,
)
from bt_api_execution.ctp_single_worker_candidate import stable_managed_command_id
from bt_api_py.ctp_service_execution import encode_ctp_provider_order_id
from bt_api_py.ctp_zero_fill_tracked_order import (
    CtpZeroFillTrackedOrderEvidenceV1,
    CtpZeroFillTrackedOrderReader,
)


def _submit_command(case, intent, reservation):
    command_id = stable_managed_command_id(
        "submit", intent.intent_id, reservation.runtime_order_id
    )
    command = case.store.read_ctp_dispatch_command(case.scope, command_id)
    assert command is not None
    assert command.operation == "SUBMIT"
    assert command.status == "COMPLETED"
    assert command.local_queue_receipt_queued is True
    return command


def _emit_ack(case, owner, intent, reservation, command):
    # Preserve the normal two-step callback history used by the CTP source
    # verifier: accepted OnRtnOrder, followed by its stable accepted projection.
    callback_fixtures._emit_order_status(
        case,
        intent,
        reservation,
        command.correlation_key.native_request_id,
        status="3",
        submit_status="5",
        traded=0,
        sequence=1,
    )
    assert owner.pump_callbacks()
    callback_fixtures._emit_order_status(
        case,
        intent,
        reservation,
        command.correlation_key.native_request_id,
        status="3",
        submit_status="3",
        traded=0,
        sequence=2,
    )
    assert owner.pump_callbacks()


def _install_native_empty_trade_query(case, monkeypatch):
    response_info = SimpleNamespace(ErrorID=0, ErrorMsg="")

    def req_qry_trade(field, request_id):
        case.api.calls.append(("ReqQryTrade", field, request_id))
        case.api.spi.OnRspQryTrade(None, response_info, request_id, True)
        return 0

    monkeypatch.setattr(case.api, "ReqQryTrade", req_qry_trade, raising=False)


def _install_native_missing_rsp_info_trade_terminal(case, monkeypatch):
    def req_qry_trade(field, request_id):
        case.api.calls.append(("ReqQryTrade", field, request_id))
        case.api.spi.OnRspQryTrade(None, None, request_id, True)
        return 0

    monkeypatch.setattr(case.api, "ReqQryTrade", req_qry_trade, raising=False)


def _native_trade(case, intent, reservation):
    return SimpleNamespace(
        BrokerID="9999",
        InvestorID="investor-1",
        UserID="investor-1",
        OrderRef=reservation.order_ref,
        ExchangeID="SHFE",
        TradeID="TRADE-" + reservation.order_ref,
        Direction="0",
        OrderSysID="SYS" + reservation.order_ref,
        ParticipantID="participant-1",
        ClientID="client-1",
        TradingRole="1",
        OffsetFlag="0",
        HedgeFlag="1",
        Price=50000.0,
        Volume=1,
        TradeDate=case.scope.trading_day,
        TradeTime="10:00:00",
        TradeType="0",
        PriceSource="0",
        TraderID="trader-1",
        OrderLocalID="local-1",
        ClearingPartID="clearing-1",
        BusinessUnit="",
        SequenceNo=1,
        TradingDay=case.scope.trading_day,
        SettlementID=1,
        BrokerOrderSeq=1,
        TradeSource="0",
        InvestUnitID="",
        InstrumentID=intent.instrument,
        ExchangeInstID=intent.instrument,
    )


def _install_native_trade_rows(case, monkeypatch, rows):
    response_info = SimpleNamespace(ErrorID=0, ErrorMsg="")

    def req_qry_trade(field, request_id):
        case.api.calls.append(("ReqQryTrade", field, request_id))
        for row in rows:
            case.api.spi.OnRspQryTrade(row, response_info, request_id, False)
        case.api.spi.OnRspQryTrade(None, response_info, request_id, True)
        return 0

    monkeypatch.setattr(case.api, "ReqQryTrade", req_qry_trade, raising=False)


def _open_zero_fill_evidence(case, monkeypatch, suffix: str):
    intent, reservation, submit_port = query_fixtures._dispatch_day_submit(
        case, suffix
    )
    command = _submit_command(case, intent, reservation)
    owner = submit_port._owner
    owner._trader._query_interval = 0.0
    reader = CtpZeroFillTrackedOrderReader(owner)
    _emit_ack(case, owner, intent, reservation, command)
    row = query_fixtures._native_order(
        case, intent=intent, reservation=reservation
    )
    query_fixtures._install_native_query_rows(case, monkeypatch, (row,))
    _install_native_empty_trade_query(case, monkeypatch)
    return intent, reservation, command, owner, reader, row


@pytest.mark.unit
def test_zero_fill_evidence_requires_callback_ack_then_exact_open_query(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation, submit_port = query_fixtures._dispatch_day_submit(
            case, "zero-fill-open-evidence"
        )
        command = _submit_command(case, intent, reservation)
        owner = submit_port._owner
        owner._trader._query_interval = 0.0
        reader = CtpZeroFillTrackedOrderReader(owner)

        # A completed local queue receipt is not provider ACK and must not
        # start an economic QueryOrder request.
        before = case.store._connection.total_changes
        pending = reader.issue(intent.intent_id, timeout=0.25)
        assert type(pending) is CtpOwnerCallbackObservationNotReadyV1
        assert pending.reason_code == "PROVIDER_ORDER_NOT_READY"
        assert case.store._connection.total_changes == before
        assert not [call for call in case.api.calls if call[0] == "ReqQryOrder"]

        _emit_ack(case, owner, intent, reservation, command)
        row = query_fixtures._native_order(
            case, intent=intent, reservation=reservation
        )
        query_fixtures._install_native_query_rows(case, monkeypatch, (row,))
        _install_native_empty_trade_query(case, monkeypatch)

        evidence = reader.issue(intent.intent_id, timeout=2.0)
        assert type(evidence) is CtpZeroFillTrackedOrderEvidenceV1
        assert evidence.scope == case.scope
        assert evidence.managed_intent_id == intent.intent_id
        assert evidence.intent_fingerprint == intent.fingerprint
        assert evidence.reservation == reservation
        assert evidence.submit_command_id == command.command_id
        assert evidence.native_request_id == command.correlation_key.native_request_id
        assert evidence.session_binding == case.session_binding
        assert evidence.callback_snapshot.command_id == command.command_id
        assert evidence.callback_snapshot.request_id == command.correlation_key.native_request_id
        assert evidence.callback_snapshot.provider_state == "ACKNOWLEDGED"
        assert evidence.callback_snapshot.volume_traded == 0
        assert evidence.callback_snapshot.trade_fact_count == 0
        assert evidence.provider_observation.state is ExecutionState.ACKED
        assert evidence.provider_observation.filled_quantity == 0
        assert evidence.provider_order_id == encode_ctp_provider_order_id(
            evidence.callback_snapshot.exchange_id,
            evidence.callback_snapshot.order_sys_id,
        )
        assert evidence.projection.provider_state == "OPEN"
        assert evidence.projection.traded_quantity == 0
        assert evidence.projection.remaining_quantity == int(intent.quantity)
        assert evidence.projection.query_complete is True
        trade_query = evidence.to_payload()["query_trade"]
        assert trade_query["schema"] == "ctp_order_target_trade_query_window.v1"
        assert trade_query["complete"] is True
        assert trade_query["is_last_seen"] is True
        assert trade_query["record_count"] == 0
        assert trade_query["target_order_trade_count"] == 0
        assert len(evidence.trade_query.window.events) == 1
        assert evidence.trade_query.window.events[0].callback_name == "OnRspQryTrade"
        empty_trade_callback = json.loads(
            evidence.trade_query.window.events[0].record_payload_json
        )
        assert empty_trade_callback["named_args"][0]["pointer_is_null"] is True
        assert empty_trade_callback["named_args"][1]["pointer_is_null"] is False
        assert next(
            field["value"]
            for field in empty_trade_callback["flattened_fields"]
            if field["argument_slot"] == 1 and field["field_name"] == "ErrorID"
        ) == 0

        # Native submit RequestID and QueryOrder request ID are separate
        # provenance. QueryOrder RequestID 41 is a query-row field, not the
        # submit callback identity.
        assert row.RequestID == 41
        assert evidence.projection.query_request_id == (
            evidence.projection.query_provenance.query_request_id
        )
        assert evidence.projection.query_request_id != row.RequestID
        assert len([call for call in case.api.calls if call[0] == "ReqQryOrder"]) == 1
        assert len([call for call in case.api.calls if call[0] == "ReqQryTrade"]) == 1

        changes_after_issue = case.store._connection.total_changes
        readbacks_before = len(
            [call for call in case.api.calls if call[0] == "ReqQryOrder"]
        )
        assert reader.readback(evidence) is evidence
        assert case.store._connection.total_changes == changes_after_issue
        assert len(
            [call for call in case.api.calls if call[0] == "ReqQryOrder"]
        ) == readbacks_before

        copied = replace(evidence)
        assert copied == evidence and copied is not evidence
        with pytest.raises(ContractValidationError):
            reader.readback(copied)


@pytest.mark.unit
def test_zero_fill_issue_passes_one_deadline_to_order_and_trade_queries(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, _reservation, _command, owner, reader, _row = _open_zero_fill_evidence(
            case, monkeypatch, "zero-fill-shared-issue-deadline"
        )
        trader = owner._trader
        observed_deadlines = {}
        query_orders_result = trader.query_orders_result
        query_trades_result = trader.query_trades_result

        def observe_orders(*args, **kwargs):
            observed_deadlines["orders"] = kwargs.get("deadline_monotonic")
            return query_orders_result(*args, **kwargs)

        def observe_trades(*args, **kwargs):
            observed_deadlines["trades"] = kwargs.get("deadline_monotonic")
            return query_trades_result(*args, **kwargs)

        monkeypatch.setattr(trader, "query_orders_result", observe_orders)
        monkeypatch.setattr(trader, "query_trades_result", observe_trades)
        started = time.monotonic()

        evidence = reader.issue(intent.intent_id, timeout=0.25)

        assert type(evidence) is CtpZeroFillTrackedOrderEvidenceV1
        assert set(observed_deadlines) == {"orders", "trades"}
        assert observed_deadlines["orders"] == observed_deadlines["trades"]
        assert started < observed_deadlines["orders"] <= started + 0.25


@pytest.mark.unit
def test_zero_fill_reader_rejects_target_trade_from_complete_query_window(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation, _command, owner, reader, row = _open_zero_fill_evidence(
            case, monkeypatch, "zero-fill-target-trade-rejected"
        )
        target_trade = _native_trade(case, intent, reservation)
        _install_native_trade_rows(case, monkeypatch, (target_trade,))

        with pytest.raises(ContractValidationError):
            reader.issue(intent.intent_id, timeout=0.25)

        assert reader._issued is None
        assert reader._attempt is None
        assert len([call for call in case.api.calls if call[0] == "ReqQryOrder"]) == 1
        assert len([call for call in case.api.calls if call[0] == "ReqQryTrade"]) == 1


@pytest.mark.unit
def test_zero_fill_reader_accepts_same_instrument_non_target_trade_pointer(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation, _command, _owner, reader, _row = _open_zero_fill_evidence(
            case, monkeypatch, "zero-fill-non-target-trade-pointer"
        )
        non_target_trade = _native_trade(case, intent, reservation)
        non_target_trade.OrderRef = "999999999998"
        non_target_trade.OrderSysID = "SYS-NON-TARGET"
        assert non_target_trade.InstrumentID == intent.instrument
        assert non_target_trade.OrderRef != reservation.order_ref

        _install_native_trade_rows(case, monkeypatch, (non_target_trade,))
        evidence = reader.issue(intent.intent_id, timeout=2.0)

        assert type(evidence) is CtpZeroFillTrackedOrderEvidenceV1
        assert evidence.trade_query.target_trade_count == 0
        assert len(evidence.trade_query.result.records) == 1
        assert evidence.trade_query.result.records[0].InstrumentID == intent.instrument
        row_callback = json.loads(
            evidence.trade_query.window.events[0].record_payload_json
        )
        assert row_callback["named_args"][0]["name"] == "pTrade"
        assert row_callback["named_args"][0]["pointer_is_null"] is False
        terminal_callback = json.loads(
            evidence.trade_query.window.events[-1].record_payload_json
        )
        assert terminal_callback["named_args"][0]["pointer_is_null"] is True


@pytest.mark.unit
def test_zero_fill_reader_rejects_null_rsp_info_trade_terminal_without_evidence(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, _reservation, _command, _owner, reader, _row = _open_zero_fill_evidence(
            case, monkeypatch, "zero-fill-null-rsp-info-terminal"
        )
        _install_native_missing_rsp_info_trade_terminal(case, monkeypatch)

        with pytest.raises(ContractValidationError):
            reader.issue(intent.intent_id, timeout=0.25)

        assert reader._issued is None
        assert reader._attempt is None
        assert len([call for call in case.api.calls if call[0] == "ReqQryOrder"]) == 1
        assert len([call for call in case.api.calls if call[0] == "ReqQryTrade"]) == 1
        events = case.store._connection.execute(
            "SELECT record_payload_json FROM ctp_dispatch_callback_ingress "
            "WHERE owner_intent_id = ? AND callback_name = 'OnRspQryTrade' "
            "ORDER BY source_sequence",
            (case.owner_handle.owner_intent_id,),
        ).fetchall()
        assert len(events) == 1
        terminal = json.loads(events[0][0])
        assert terminal["named_args"][0]["pointer_is_null"] is True
        assert terminal["named_args"][1]["pointer_is_null"] is True
        error_id_fields = [
            field
            for field in terminal["flattened_fields"]
            if field["argument_slot"] == 1 and field["field_name"] == "ErrorID"
        ]
        assert error_id_fields
        assert not any(
            field["present"] is True
            and field["scalar_captured"] is True
            and type(field["value"]) is int
            and field["value"] == 0
            for field in error_id_fields
        )


@pytest.mark.unit
def test_zero_fill_query_uses_source_monotonic_clock_domain(tmp_path, monkeypatch):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, _reservation, _command, _owner, reader, _row = _open_zero_fill_evidence(
            case, monkeypatch, "zero-fill-monotonic-clock-domain"
        )

        # Keep the CTP source validator's declared time.monotonic() domain,
        # then model the adjacent nanosecond sample one tick below its result.
        source_clock = target_query_module.time
        validate_source = reader._query._validate_query_source
        validated_sources = []

        def capture_source(*args, **kwargs):
            source = validate_source(*args, **kwargs)
            validated_sources.append(source)
            monkeypatch.setattr(
                target_query_module,
                "time",
                SimpleNamespace(
                    monotonic=source_clock.monotonic,
                    monotonic_ns=lambda: int(
                        source.completed_monotonic * 1_000_000_000
                    ) - 1,
                ),
            )
            return source

        monkeypatch.setattr(reader._query, "_validate_query_source", capture_source)

        evidence = reader.issue(intent.intent_id, timeout=0.25)
        assert validated_sources
        assert type(evidence) is CtpZeroFillTrackedOrderEvidenceV1
        assert evidence.provider_observation.state is ExecutionState.ACKED
        assert evidence.callback_snapshot.volume_traded == 0

@pytest.mark.unit
def test_zero_fill_evidence_readback_rejects_source_change_wrong_thread_and_expiry(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation, command, owner, reader, _row = _open_zero_fill_evidence(
            case, monkeypatch, "zero-fill-readback-boundaries"
        )
        evidence = reader.issue(intent.intent_id, timeout=0.25)
        assert type(evidence) is CtpZeroFillTrackedOrderEvidenceV1

        failures = []

        def read_on_wrong_thread():
            try:
                reader.readback(evidence)
            except BaseException as exc:  # bounded handoff to assert thread denial
                failures.append(exc)

        thread = threading.Thread(
            target=read_on_wrong_thread, name="zero-fill-wrong-owner-thread"
        )
        thread.start()
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        assert len(failures) == 1
        assert type(failures[0]) is ContractValidationError

        # Replace only this module's clock reference; no process-wide clock is
        # changed. The issued evidence remains the exact object, but is expired.
        monkeypatch.setattr(
            zero_fill_module,
            "time",
            SimpleNamespace(monotonic_ns=lambda: evidence.expires_at_ns),
        )
        with pytest.raises(ContractValidationError):
            reader.readback(evidence)


@pytest.mark.unit
def test_zero_fill_evidence_readback_rejects_new_callback_source(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation, command, owner, reader, _row = _open_zero_fill_evidence(
            case, monkeypatch, "zero-fill-source-revalidation"
        )
        evidence = reader.issue(intent.intent_id, timeout=0.25)
        assert type(evidence) is CtpZeroFillTrackedOrderEvidenceV1

        # A later valid callback changes the durable source sequence even
        # though the order remains ACKED and unfilled.
        callback_fixtures._emit_order_status(
            case,
            intent,
            reservation,
            command.correlation_key.native_request_id,
            status="3",
            submit_status="3",
            traded=0,
            sequence=3,
        )
        assert owner.pump_callbacks()
        before_readback = case.store._connection.total_changes
        query_calls_before = len(
            [call for call in case.api.calls if call[0] == "ReqQryOrder"]
        )
        with pytest.raises(ContractValidationError):
            reader.readback(evidence)
        assert case.store._connection.total_changes == before_readback
        assert len(
            [call for call in case.api.calls if call[0] == "ReqQryOrder"]
        ) == query_calls_before == 1


@pytest.mark.unit
def test_zero_fill_evidence_rejects_mismatched_open_query_identity(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation, command, owner, reader, _row = _open_zero_fill_evidence(
            case, monkeypatch, "zero-fill-query-identity-mismatch"
        )
        wrong_row = query_fixtures._native_order(
            case, intent=intent, reservation=reservation
        )
        wrong_row.OrderSysID = "SYS-OTHER"
        query_fixtures._install_native_query_rows(case, monkeypatch, (wrong_row,))

        with pytest.raises(ContractValidationError):
            reader.issue(intent.intent_id, timeout=0.25)

        assert len([call for call in case.api.calls if call[0] == "ReqQryOrder"]) == 1
        assert case.store._connection.execute(
            "SELECT COUNT(*) FROM ctp_order_target_projections "
            "WHERE managed_intent_id = ?",
            (intent.intent_id,),
        ).fetchone()[0] == 0


@pytest.mark.unit
def test_zero_fill_evidence_is_not_issued_if_callback_changes_during_query(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent, reservation, command, owner, reader, _row = _open_zero_fill_evidence(
            case, monkeypatch, "zero-fill-issue-revalidation"
        )
        query_and_issue = reader._query.query_and_issue

        def query_then_change_source(*args, **kwargs):
            handle = query_and_issue(*args, **kwargs)
            callback_fixtures._emit_order_status(
                case,
                intent,
                reservation,
                command.correlation_key.native_request_id,
                status="3",
                submit_status="3",
                traded=0,
                sequence=3,
            )
            assert owner.pump_callbacks()
            return handle

        monkeypatch.setattr(reader._query, "query_and_issue", query_then_change_source)
        with pytest.raises(ContractValidationError):
            reader.issue(intent.intent_id, timeout=0.25)

        assert reader._issued is None
        assert reader._attempt is None
        assert len([call for call in case.api.calls if call[0] == "ReqQryOrder"]) == 1
