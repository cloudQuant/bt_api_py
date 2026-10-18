"""Runtime-built CTP zero-fill tracking against the real durable Risk stack.

The Store, execution service adapter, ManagedExecutionFacade, TraderClient,
owner, callback verifier, zero-fill reader, Risk gate, journal, and bridge are
production objects. The native API and its SPI-producing callbacks are local
synthetic leaves. This is source-fake integration evidence, not exchange,
credential, account-cutover, or real native acceptance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
import test_ctp_execution_runtime as runtime_fixtures
import test_ctp_runtime_order_chain as chain_fixtures
from bt_api_execution import ExecutionState
from bt_api_py.ctp_zero_fill_tracked_order import (
    CtpZeroFillTrackedOrderEvidenceV1,
    CtpZeroFillTrackedOrderReader,
)
from bt_api_risk import DispatchResolutionReceiptStatus
from bt_api_py.service_risk_order_journal import (
    _ctp_zero_fill_dispatch_history_valid,
)


def _rehash_evidence_events(
    order_event, fill_event, alteration, *, mirror_fingerprint=False
):
    """Build detached DTO variants with matching evidence digests and copies."""

    order_payload = json.loads(json.dumps(order_event.payload))
    fill_payload = json.loads(json.dumps(fill_event.payload))
    evidence = order_payload["record"]["evidence"]
    intent = evidence["intent"]
    if alteration == "fingerprint":
        intent["fingerprint"] = (
            "e" if intent["fingerprint"] != "e" * 64 else "f"
        ) * 64
    elif alteration == "scope":
        evidence["scope"]["strategy_id"] = "foreign-strategy"
    elif alteration == "position_effect":
        intent["position_effect"] = "CLOSE"
    elif alteration == "reduce_only":
        intent["reduce_only"] = True
    else:
        raise AssertionError(f"unknown detached evidence alteration: {alteration}")

    digest_body = {
        key: item for key, item in evidence.items() if key != "evidence_sha256"
    }
    evidence_sha256 = hashlib.sha256(
        json.dumps(
            digest_body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    ).hexdigest()
    evidence["evidence_sha256"] = evidence_sha256

    for payload in (order_payload, fill_payload):
        record = payload["record"]
        record["evidence"] = json.loads(json.dumps(evidence))
        record["evidence_sha256"] = evidence_sha256
        if mirror_fingerprint:
            record["execution_intent_sha256"] = intent["fingerprint"]

    return replace(order_event, payload=order_payload), replace(
        fill_event, payload=fill_payload
    )


def _install_submit_query_rows(case, api, monkeypatch, callback_case, intent_id):
    """Install a fake native query leaf that emits the exact persisted target."""

    calls: list[int] = []
    response_info = SimpleNamespace(ErrorID=0, ErrorMsg="synthetic query response")

    def req_qry_order(field, request_id):
        api.calls.append(("ReqQryOrder", field, request_id))
        calls.append(request_id)
        store = case.resources.values["store"]
        intent = store.get_intent(intent_id, scope=case.execution_scope)
        reservation = store.read_ctp_order_identity(case.execution_scope, intent_id)
        assert intent is not None and reservation is not None
        row_case = SimpleNamespace(
            api=api,
            scope=case.execution_scope,
            session_binding=callback_case.session_binding,
        )
        row = chain_fixtures.query_fixtures._native_order(
            row_case, intent=intent, reservation=reservation
        )
        api.spi.OnRspQryOrder(row, response_info, request_id, False)
        api.spi.OnRspQryOrder(None, response_info, request_id, True)
        return 0

    monkeypatch.setattr(api, "ReqQryOrder", req_qry_order, raising=False)
    return calls


def _prepare_runtime_order(case, monkeypatch, intent_id):
    component = runtime_fixtures._build_execution_component(case, monkeypatch)
    chain_fixtures._seed_synthetic_order_ref_cutover(case)
    reserve_errors = chain_fixtures._observe_authenticated_reserve(case, monkeypatch)
    chain_fixtures._observe_submit_authorized(case, monkeypatch)
    api = case.factory.instances[0]
    callback_case, session_binding, req_order_insert = (
        chain_fixtures._install_submit_callback(case, api, intent_id)
    )
    monkeypatch.setattr(api, "ReqOrderInsert", req_order_insert, raising=False)
    query_calls = _install_submit_query_rows(
        case, api, monkeypatch, callback_case, intent_id
    )
    return component, api, callback_case, session_binding, reserve_errors, query_calls


def _observe_zero_fill_reader(monkeypatch):
    original_issue = CtpZeroFillTrackedOrderReader.issue
    original_readback = CtpZeroFillTrackedOrderReader.readback
    issued = []
    readbacks = []

    def observe_issue(reader, managed_intent_id, *, timeout=5.0):
        result = original_issue(reader, managed_intent_id, timeout=timeout)
        if type(result) is CtpZeroFillTrackedOrderEvidenceV1:
            issued.append(result)
        return result

    def observe_readback(reader, evidence):
        readbacks.append(evidence)
        return original_readback(reader, evidence)

    monkeypatch.setattr(CtpZeroFillTrackedOrderReader, "issue", observe_issue)
    monkeypatch.setattr(CtpZeroFillTrackedOrderReader, "readback", observe_readback)
    return issued, readbacks


def _install_hook_counter(bridge, monkeypatch):
    original = bridge.after_evidenced_observation
    calls = []
    errors = []

    def observe(record, event, *, writer_lease):
        calls.append(
            (record.intent_id, event.event_type, record.state, record, event, writer_lease)
        )
        try:
            return original(record, event, writer_lease=writer_lease)
        except Exception as error:
            frames = []
            trace = error.__traceback__
            while trace is not None:
                name = trace.tb_frame.f_code.co_filename.replace(chr(92), "/")
                if "/bt_api_py/" in name or "/bt_api_risk/" in name:
                    frames.append((name.rsplit("/", 1)[-1], trace.tb_frame.f_code.co_name, trace.tb_lineno))
                trace = trace.tb_next
            code = getattr(error, "code", None)
            errors.append({"type": type(error).__name__, "code": code if type(code) is str and code.replace("_", "").isalnum() else None, "frames": frames[-4:]})
            raise

    monkeypatch.setattr(bridge, "after_evidenced_observation", observe, raising=True)
    return calls, errors


def _journal_events(case, bridge, intent_id):
    command = case.journal.get_command(
        bridge.account_scope, chain_fixtures._PRINCIPAL, intent_id
    )
    assert command is not None
    events = tuple(
        case.journal.get_event(
            bridge.account_scope,
            chain_fixtures._PRINCIPAL,
            intent_id,
            revision,
        )
        for revision in range(1, command.latest_revision + 1)
    )
    return command, tuple(event for event in events if event is not None)


def _assert_current_order_evidence_events(events, evidence, *, expected_quantity):
    """Check the bridge records current CTP evidence without claiming history."""

    order_events = [event for event in events if event.event_type == "ORDER_OBSERVATION"]
    fill_events = [event for event in events if event.event_type == "FILL_OBSERVATION"]
    assert len(order_events) == 1
    assert len(fill_events) == 1

    order_payload = order_events[0].payload
    fill_payload = fill_events[0].payload
    order_record = order_payload["record"]
    fill_record = fill_payload["record"]
    assert type(order_record) is dict
    assert type(fill_record) is dict

    for record in (order_record, fill_record):
        assert record["schema"] == "ctp-risk-current-order-evidence-v1"
        assert record["evidence_kind"] == "CTP_CALLBACK_AND_QUERY_ORDER_CURRENT_STATE"
        assert record["evidence"] == json.loads(json.dumps(evidence.to_payload()))
        assert record["evidence_sha256"] == evidence.evidence_sha256
        assert record["observation_scope"] == "CURRENT_ORDER_PROJECTION"
        assert record["fill_history_completeness"] == "UNOBSERVED"
        assert record["trade_history_observed"] is False
        assert record["trade_fact_count"] == 0
        assert Decimal(record["trade_notional"]) == Decimal("0")
        assert record["cumulative_commission"] is None
        assert record["cumulative_commission_currency"] is None
        assert record["commission_quality"] == "INCOMPLETE"
        assert "get_deals" not in record
        assert "complete" not in record
        assert "records" not in record

    assert order_record["provider_state"] == "OPEN"
    assert order_record["provider_status"] == "ACKED"
    assert fill_record["requested_quantity"] == format(expected_quantity, "f")
    assert "get_deals" not in order_payload
    assert "get_deals" not in fill_payload


def _risk_resolution(case, bridge, intent_id):
    binding = bridge.read_dispatch_binding(chain_fixtures._PRINCIPAL, intent_id)
    assert binding is not None
    receipt = case.risk_gate.dispatch_resolution_receipt(binding["permit_id"])
    return binding, receipt


@pytest.mark.unit
def test_runtime_ack_tracks_exact_zero_fill_risk_proof_and_replay_is_read_only(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        intent_id = "ctp-runtime-zero-fill-risk-ack"
        component, api, _callback_case, _session_binding, reserve_errors, query_calls = (
            _prepare_runtime_order(case, monkeypatch, intent_id)
        )
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None
        bridge = resources.values["admission"]["order_gate"]
        hook_calls, hook_errors = _install_hook_counter(bridge, monkeypatch)
        issued_evidence, readback_evidence = _observe_zero_fill_reader(monkeypatch)
        context = chain_fixtures._service_context(case)
        arguments = chain_fixtures._order_arguments(intent_id)

        submitted = component.handlers["orders.submit"](context, arguments)
        assert reserve_errors == []
        assert case.authority_verify_errors == []
        assert case.native_lease_errors == []
        assert case.native_submit_errors == []
        assert case.submit_authorized_errors == []
        assert submitted["ok"] is True
        assert submitted["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert hook_errors == [], hook_errors
        assert len(hook_calls) == 1
        assert hook_calls[0][0] == intent_id
        assert len(issued_evidence) == 1
        assert len(readback_evidence) == 2
        assert all(evidence is issued_evidence[0] for evidence in readback_evidence)
        evidence = issued_evidence[0]
        assert evidence.managed_intent_id == intent_id
        assert evidence.callback_snapshot.provider_state == "ACKNOWLEDGED"
        assert evidence.callback_snapshot.volume_traded == 0
        assert evidence.callback_snapshot.trade_fact_count == 0
        assert evidence.provider_observation.state is ExecutionState.ACKED
        assert evidence.provider_observation.filled_quantity == 0
        assert evidence.projection.provider_state == "OPEN"
        assert evidence.projection.traded_quantity == 0
        assert evidence.projection.query_complete is True
        assert len(query_calls) == 1
        assert chain_fixtures._counts(api) == (1, 1, 0)

        binding, receipt = _risk_resolution(case, bridge, intent_id)
        assert receipt.status is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        assert receipt.dispatch_latch_active is False
        assert receipt.claim is not None and receipt.proof is not None
        assert receipt.proof.permit_id == binding["permit_id"]
        assert receipt.proof.intent_id == intent_id
        assert receipt.proof.filled_quantity == 0
        assert receipt.proof.trade_count == 0
        assert receipt.proof.provider_order_id
        journal_command, journal_events = _journal_events(case, bridge, intent_id)
        assert journal_command.state == "ACKED_TRACKED"
        assert "PROOF_READY" in {event.event_type for event in journal_events}
        assert journal_events[-1].event_type == "ACKED_TRACKED"
        assert journal_events[-1].payload["filled_quantity"] == 0
        assert journal_events[-1].payload["trade_count"] == 0
        _assert_current_order_evidence_events(
            journal_events,
            evidence,
            expected_quantity=Decimal(arguments["quantity"]),
        )

        order_event = next(
            event for event in journal_events if event.event_type == "ORDER_OBSERVATION"
        )
        fill_event = next(
            event for event in journal_events if event.event_type == "FILL_OBSERVATION"
        )
        proof_ready_event = next(
            event for event in journal_events if event.event_type == "PROOF_READY"
        )
        proof_reference = proof_ready_event.payload["exposure_reference"]

        def history_valid(candidate_order, candidate_fill):
            return _ctp_zero_fill_dispatch_history_valid(
                binding,
                claim=receipt.claim,
                risk_scope_key=bridge.account_scope.key,
                command=journal_command,
                proof=receipt.proof,
                reference=proof_reference,
                order_event=candidate_order,
                fill_event=candidate_fill,
            )

        assert history_valid(order_event, fill_event) is True
        for alteration, mirror_fingerprint in (
            ("fingerprint", True),
            ("scope", False),
            ("position_effect", False),
            ("reduce_only", False),
        ):
            # These are detached event DTOs for the pure validator only. They
            # are not appended to or represented as accepted journal events.
            altered_order, altered_fill = _rehash_evidence_events(
                order_event,
                fill_event,
                alteration,
                mirror_fingerprint=mirror_fingerprint,
            )
            assert history_valid(altered_order, altered_fill) is False, alteration

        unchanged_command, unchanged_events = _journal_events(
            case, bridge, intent_id
        )
        assert unchanged_command == journal_command
        assert unchanged_events == journal_events
        assert case.risk_gate.dispatch_resolution_receipt(binding["permit_id"]) == receipt

        risk_snapshot = case.risk_gate.snapshot(bridge.account_scope)
        assert risk_snapshot["increase_count"] == 1
        assert Decimal(risk_snapshot["increase_notional"]) == Decimal("200")

        revision_before_replay = journal_command.latest_revision
        # The facade's exact record replay has no new event and therefore
        # skips the hook. Exercise the bridge's own exact-source-event replay
        # branch with the same persisted record, event, and lease captured on
        # its first invocation.
        original_record, original_event, original_lease = hook_calls[0][3:]
        assert bridge.after_evidenced_observation(
            original_record,
            original_event,
            writer_lease=original_lease,
        ) is None
        assert len(hook_calls) == 2
        assert len(issued_evidence) == 1
        assert len(readback_evidence) == 2
        assert all(evidence is issued_evidence[0] for evidence in readback_evidence)
        assert len(query_calls) == 1
        assert chain_fixtures._counts(api) == (1, 1, 0)
        exact_replay_command, exact_replay_events = _journal_events(
            case, bridge, intent_id
        )
        assert exact_replay_command.latest_revision == revision_before_replay
        assert exact_replay_events == journal_events
        assert case.risk_gate.dispatch_resolution_receipt(binding["permit_id"]) == receipt
        assert case.risk_gate.snapshot(bridge.account_scope) == risk_snapshot

        replay = component.handlers["orders.submit"](context, arguments)
        assert replay["ok"] is True
        assert replay["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert len(hook_calls) == 2
        assert len(query_calls) == 1
        assert chain_fixtures._counts(api) == (1, 1, 0)
        replay_command, replay_events = _journal_events(case, bridge, intent_id)
        assert replay_command.latest_revision == revision_before_replay
        assert replay_events == journal_events
        assert case.risk_gate.dispatch_resolution_receipt(binding["permit_id"]) == receipt
        assert case.risk_gate.snapshot(bridge.account_scope) == risk_snapshot

        component.close()
        case.component = None
        assert runtime.status.closed is True


@pytest.mark.unit
def test_later_partial_fill_without_fee_keeps_zero_fill_risk_reservation_counted(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        intent_id = "ctp-runtime-zero-fill-risk-then-partial"
        component, api, callback_case, _session_binding, reserve_errors, query_calls = (
            _prepare_runtime_order(case, monkeypatch, intent_id)
        )
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None
        bridge = resources.values["admission"]["order_gate"]
        hook_calls, hook_errors = _install_hook_counter(bridge, monkeypatch)
        context = chain_fixtures._service_context(case)
        submitted = component.handlers["orders.submit"](
            context, chain_fixtures._order_arguments(intent_id)
        )
        assert reserve_errors == []
        assert submitted["ok"] is True
        assert submitted["result"]["record"]["state"] == ExecutionState.ACKED.value
        assert hook_errors == [], hook_errors
        assert len(hook_calls) == 1

        binding_before, receipt_before = _risk_resolution(case, bridge, intent_id)
        assert receipt_before.status is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        journal_command_before, journal_events_before = _journal_events(
            case, bridge, intent_id
        )
        risk_snapshot_before = case.risk_gate.snapshot(bridge.account_scope)

        reservation, command_id, command = chain_fixtures._submit_binding(case, intent_id)
        intent = resources.values["store"].get_intent(
            intent_id, scope=case.execution_scope
        )
        assert intent is not None
        callback_fixtures = chain_fixtures.callback_fixtures
        callback_fixtures._emit_trade(
            callback_case,
            intent,
            reservation,
            trade_id="RUNTIME-ZERO-FILL-PARTIAL-001",
            sequence=2,
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
            sequence=3,
        )
        applied = runtime._execution_owner.pump_callbacks(32)
        assert len(applied) == 2
        trade_facts = resources.values["store"].read_ctp_dispatch_trade_facts(
            case.execution_scope, command_id
        )
        assert len(trade_facts.trades) == 1
        trade = trade_facts.trades[0]
        assert trade.trade_id == "RUNTIME-ZERO-FILL-PARTIAL-001"
        assert trade.trade_quantity == 1
        assert trade.cumulative_trade_quantity == 1
        assert trade.cumulative_commission is None
        assert trade.commission_quality == "INCOMPLETE"

        # The public service query remains a read of Execution state. Runtime
        # reconciliation is the existing callback-backed path that persists
        # the later observation and exercises the bridge hook.
        runtime._next_reconciliation_ns = 0
        with pytest.raises(runtime_fixtures.CtpExecutionRuntimeError) as caught:
            runtime.maintain(frozenset({case.instance_id}))
        assert caught.value.code == "EXECUTION_RECONCILIATION_REVIEW_REQUIRED"
        assert runtime.status.ready is False
        record = resources.values["store"].get(intent_id, scope=case.execution_scope)
        assert record is not None
        assert record.state is ExecutionState.PARTIALLY_FILLED
        assert record.review_required is True
        assert len(hook_calls) == 2
        assert len(query_calls) == 1
        assert chain_fixtures._counts(api) == (1, 1, 0)

        binding, receipt = _risk_resolution(case, bridge, intent_id)
        assert binding == binding_before
        assert receipt == receipt_before
        assert receipt.status is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        assert receipt.dispatch_latch_active is False
        journal_command, journal_events = _journal_events(case, bridge, intent_id)
        assert journal_command.latest_revision == journal_command_before.latest_revision
        assert journal_events == journal_events_before
        assert journal_command.state == "ACKED_TRACKED"
        assert journal_events[-1].event_type == "ACKED_TRACKED"
        assert case.risk_gate.dispatch_resolution_receipt(binding["permit_id"]) == receipt
        risk_snapshot = case.risk_gate.snapshot(bridge.account_scope)
        assert risk_snapshot == risk_snapshot_before
        assert risk_snapshot["increase_count"] == 1
        assert Decimal(risk_snapshot["increase_notional"]) == Decimal("200")

        component.close()
        case.component = None
        assert runtime.status.closed is True


@pytest.mark.unit
def test_zero_fill_reader_rejects_equal_copy_before_risk_resolution(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        intent_id = "ctp-runtime-zero-fill-risk-copy-rejected"
        component, api, _callback_case, _session_binding, reserve_errors, query_calls = (
            _prepare_runtime_order(case, monkeypatch, intent_id)
        )
        runtime = case.provider.last_runtime
        resources = case.resources
        assert runtime is not None and resources is not None
        bridge = resources.values["admission"]["order_gate"]
        hook_calls, hook_errors = _install_hook_counter(bridge, monkeypatch)
        original_issue = CtpZeroFillTrackedOrderReader.issue
        original_readback = CtpZeroFillTrackedOrderReader.readback
        issued = []
        readback_attempts = []

        def issue_equal_copy(reader, managed_intent_id, *, timeout=5.0):
            result = original_issue(reader, managed_intent_id, timeout=timeout)
            if type(result) is CtpZeroFillTrackedOrderEvidenceV1:
                copied = replace(result)
                assert copied == result and copied is not result
                issued.append((result, copied))
                return copied
            return result

        def observe_readback(reader, evidence):
            readback_attempts.append(evidence)
            return original_readback(reader, evidence)

        monkeypatch.setattr(
            CtpZeroFillTrackedOrderReader, "issue", issue_equal_copy, raising=True
        )
        monkeypatch.setattr(
            CtpZeroFillTrackedOrderReader, "readback", observe_readback, raising=True
        )
        submitted = component.handlers["orders.submit"](
            chain_fixtures._service_context(case),
            chain_fixtures._order_arguments(intent_id),
        )
        assert reserve_errors == []
        assert submitted["ok"] is True
        assert issued and len(issued) == 1
        assert len(readback_attempts) == 1
        assert readback_attempts[0] is issued[0][1]
        assert len(hook_calls) == 1
        assert hook_calls[0][0] == intent_id
        assert len(query_calls) == 1
        assert chain_fixtures._counts(api) == (1, 1, 0)

        binding, receipt = _risk_resolution(case, bridge, intent_id)
        assert receipt.status is DispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE
        assert receipt.dispatch_latch_active is True
        journal_command, journal_events = _journal_events(case, bridge, intent_id)
        assert journal_command.state == "DISPATCHING"
        assert "PROOF_READY" not in {event.event_type for event in journal_events}
        assert "ACKED_TRACKED" not in {event.event_type for event in journal_events}
        record = resources.values["store"].get(intent_id, scope=case.execution_scope)
        assert record is not None and record.review_required is True
        assert case.risk_gate.snapshot(bridge.account_scope)["increase_count"] == 1

        component.close()
        case.component = None
        assert runtime.status.closed is True
