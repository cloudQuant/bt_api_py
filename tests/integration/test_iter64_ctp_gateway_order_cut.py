"""Explicit CTP cut coordination with synthetic native callbacks and SQLite."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from bt_api_py.ctp_gateway_order_cut import (
    CtpGatewayOrderCutCoordinator,
    CtpGatewayOrderCutError,
    _CutBusyError,
)
from tests.integration.test_iter64_ctp_observed_order_source import selected

pytest_plugins = ("tests.integration.test_iter63_ctp_gateway_return_maintenance",)


def _coordinator(real_owner, monkeypatch):
    maintenance, session, native, journal, helper = selected(real_owner)
    tracker = maintenance._observed_order_source
    # Provisional seam stubs exercise only failure/custody paths; end-to-end
    # candidate tests below use the real tracker methods once installed.
    monkeypatch.setattr(
        tracker, "ingest_query_order_row", lambda *_a, **_k: "0" * 64, raising=False
    )
    monkeypatch.setattr(tracker, "verify_next_query_receipt", lambda **_k: True, raising=False)
    monkeypatch.setattr(tracker, "record_query_cut", lambda **_k: None, raising=False)
    return (
        CtpGatewayOrderCutCoordinator(session, maintenance, timeout=1),
        maintenance,
        session,
        native,
        journal,
    )


def test_missing_tracker_interface_writes_gap_without_query(real_owner, monkeypatch):
    coordinator, maintenance, session, native, _ = _coordinator(real_owner, monkeypatch)
    monkeypatch.setattr(maintenance._observed_order_source, "ingest_query_order_row", None)
    assert not coordinator.begin()
    assert coordinator.status().state == "GAP"
    assert maintenance.observed_order_source_status().status == "GAP"
    assert native.calls == [] and session._order_query_cut is None
    assert coordinator.close()


def test_failed_gap_write_is_unknown_and_never_silently_closes(real_owner, monkeypatch):
    coordinator, maintenance, _, native, _ = _coordinator(real_owner, monkeypatch)
    tracker = maintenance._observed_order_source
    original = tracker.invalidate
    monkeypatch.setattr(tracker, "ingest_query_order_row", None)
    monkeypatch.setattr(tracker, "invalidate", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert not coordinator.begin()
    assert coordinator.status().state == "UNKNOWN" and not coordinator.close()
    assert native.calls == []
    monkeypatch.setattr(tracker, "invalidate", original)
    assert coordinator.close()
    assert tracker.status().status == "GAP"


def test_busy_begin_does_not_block_or_poison_source(real_owner, monkeypatch):
    coordinator, maintenance, session, _, _ = _coordinator(real_owner, monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def hold():
        with session._trader._query_state_lock:
            entered.set()
            assert release.wait(3)

    worker = threading.Thread(target=hold)
    worker.start()
    assert entered.wait(1)
    try:
        assert not coordinator.begin()
        assert coordinator.status().state == "IDLE" and coordinator.status().reason == "BUSY"
        assert maintenance.observed_order_source_status().status == "UNVERIFIED"
    finally:
        release.set()
        worker.join(1)
    assert coordinator.close()


def test_cancel_inflight_persists_gap_and_retains_pin_until_terminal(real_owner, monkeypatch):
    coordinator, maintenance, session, native, _ = _coordinator(real_owner, monkeypatch)
    sent = []
    monkeypatch.setattr(native, "ReqQryOrder", lambda _field, rid: sent.append(rid) or 0)
    session._trader._query_interval = 0
    assert coordinator.begin() and coordinator.advance()
    assert sent and coordinator.status().pin_retained
    assert not coordinator.close()
    assert maintenance.observed_order_source_status().status == "GAP"
    assert session._order_query_cut is not None
    native.client._spi.OnRspQryOrder(None, None, sent[0], True)
    assert coordinator.close()
    assert session._order_query_cut is None


def test_wrong_owner_does_not_call_source(real_owner, monkeypatch):
    coordinator, _, _, _, _ = _coordinator(real_owner, monkeypatch)
    errors = []

    def wrong_owner():
        for action in (
            coordinator.status,
            coordinator.begin,
            coordinator.advance,
            coordinator.candidate,
            coordinator.close,
        ):
            try:
                action()
            except CtpGatewayOrderCutError as exc:
                errors.append(str(exc))

    worker = threading.Thread(target=wrong_owner)
    worker.start()
    worker.join(1)
    assert errors == ["ORDER_CUT_WRONG_OWNER"] * 5
    assert coordinator.close()


def test_waits_for_original_callback_receipt_prefix_before_cut_commit(real_owner, monkeypatch):
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    coordinator, maintenance, session, native, _ = _coordinator(real_owner, monkeypatch)
    tracker = maintenance._observed_order_source
    attempts = []
    monkeypatch.setattr(
        tracker, "record_query_cut", lambda **kwargs: attempts.append(kwargs) or None
    )
    session._trader._query_interval = 0

    def submit(_field, request_id):
        native.client._spi.OnRtnOrder(
            SimpleNamespace(**_order(TradingDay=tracker._binding.trading_day))
        )
        native.client._handle_query_callback("orders", None, None, request_id, True)
        return 0

    monkeypatch.setattr(native, "ReqQryOrder", submit)
    assert coordinator.begin() and coordinator.advance()
    assert coordinator.status().state == "WAITING_RECEIPTS"
    assert coordinator.advance() and attempts == []
    assert coordinator.status().pin_retained
    maintenance.poll()
    assert tracker.status().source_prefix == 1
    assert coordinator.advance() and attempts == []
    assert coordinator.status().state == "COMMITTING"
    assert coordinator.advance() and len(attempts) == 1
    assert coordinator.status().state == "COMMITTING" and coordinator.candidate() is None
    assert coordinator.close()


def test_projects_one_exact_result_row_per_step_without_requery(real_owner, monkeypatch):
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    coordinator, maintenance, session, native, _ = _coordinator(real_owner, monkeypatch)
    tracker = maintenance._observed_order_source
    native.rows["orders"] = [
        _order(TradingDay=tracker._binding.trading_day, OrderRef="first"),
        _order(TradingDay=tracker._binding.trading_day, OrderRef="second"),
    ]
    observed = []

    def ingest(ingestor, *, handle, result, row_index):
        observed.append((ingestor, handle, result, row_index))
        return f"{row_index + 1:064x}"

    monkeypatch.setattr(tracker, "ingest_query_order_row", ingest)
    session._trader._query_interval = 0
    assert coordinator.begin() and coordinator.advance()
    assert coordinator.status().state == "PROJECTING" and not observed
    assert coordinator.advance() and [item[3] for item in observed] == [0]
    assert coordinator.advance() and [item[3] for item in observed] == [0, 1]
    assert all(item[0] is maintenance._ingestor for item in observed)
    assert all(item[1] is session._order_query_cut.handle for item in observed)
    assert all(item[2] is session._order_query_cut.result for item in observed)
    assert native.calls.count("orders") == 1
    assert coordinator.close()


def test_projection_failure_durably_marks_gap_and_keeps_pin(real_owner, monkeypatch):
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    coordinator, maintenance, session, native, _ = _coordinator(real_owner, monkeypatch)
    tracker = maintenance._observed_order_source
    native.rows["orders"] = [_order(TradingDay=tracker._binding.trading_day)]

    def fail_row(*_args, **_kwargs):
        raise ValueError("synthetic missing instrument")

    monkeypatch.setattr(tracker, "ingest_query_order_row", fail_row)
    session._trader._query_interval = 0
    assert coordinator.begin() and coordinator.advance()
    assert not coordinator.advance()
    assert coordinator.status().state == "GAP" and coordinator.status().pin_retained
    assert tracker.status().status == "GAP" and coordinator.candidate() is None
    assert session._order_query_cut is not None
    assert coordinator.close()


def test_busy_cut_verification_waits_without_creating_gap(real_owner, monkeypatch):
    coordinator, maintenance, session, _, _ = _coordinator(real_owner, monkeypatch)
    session._trader._query_interval = 0
    assert coordinator.begin() and coordinator.advance()
    assert coordinator.status().state == "WAITING_RECEIPTS"
    entered, release = threading.Event(), threading.Event()

    def hold():
        with session._trader._query_state_lock:
            entered.set()
            assert release.wait(3)

    worker = threading.Thread(target=hold)
    worker.start()
    assert entered.wait(1)
    try:
        assert coordinator.advance()
        assert coordinator.status().state == "WAITING_RECEIPTS"
        assert maintenance.observed_order_source_status().status == "UNVERIFIED"
    finally:
        release.set()
        worker.join(1)
    assert coordinator.close()


def _real_coordinator(real_owner):
    maintenance, session, native, journal, helper = selected(real_owner)
    session._trader._query_interval = 0
    return (
        CtpGatewayOrderCutCoordinator(session, maintenance, timeout=2),
        maintenance,
        session,
        native,
        journal,
        helper,
    )


def _finish(coordinator):
    for _ in range(40):
        if not coordinator.advance():
            return
    raise AssertionError("cut did not finish within bounded synthetic polls")


def test_real_tracker_query_rows_persisted_and_empty_query_keeps_prior_fact(real_owner):
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    coordinator, maintenance, session, native, journal, helper = _real_coordinator(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    maintenance.poll()
    with journal._transaction() as db:
        before = db.execute("SELECT count(*) FROM direct_execution_facts").fetchone()[0]
    assert before == 1
    native.rows["orders"] = []
    assert coordinator.begin()
    _finish(coordinator)
    candidate = coordinator.candidate()
    assert candidate is not None and candidate.status == "CANDIDATE"
    assert candidate.query_fact_digests == () and not candidate.source_history_has_gap
    assert not coordinator.status().pin_retained
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM direct_execution_facts").fetchone()[0] == before
    assert coordinator.close()


def test_real_tracker_persists_each_query_row_without_extra_query(real_owner, monkeypatch):
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    coordinator, maintenance, _, native, journal, helper = _real_coordinator(real_owner)
    native.rows["orders"] = [
        _order(TradingDay=helper.TODAY, OrderRef="11", OrderSysID="sys-11"),
        _order(TradingDay=helper.TODAY, OrderRef="12", OrderSysID="sys-12"),
    ]
    tracker = maintenance._observed_order_source
    errors = []
    original = tracker.ingest_query_order_row
    def capture_error(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except Exception as error:
            errors.append((type(error).__name__, str(error)))
            raise
    monkeypatch.setattr(tracker, "ingest_query_order_row", capture_error)
    assert coordinator.begin()
    _finish(coordinator)
    candidate = coordinator.candidate()
    assert candidate is not None and len(candidate.query_fact_digests) == 2, (coordinator.status(), errors)
    assert native.calls.count("orders") == 1
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM direct_execution_facts").fetchone()[0] == 2
        assert tuple(db.execute(
            "SELECT status,query_row_count FROM observed_order_source_cuts"
        ).fetchone()) == ("CANDIDATE", 2)
    assert maintenance.observed_order_source_status().status != "GAP"
    assert coordinator.close()


def test_real_tracker_unkeyed_query_row_writes_gap_and_retains_pin(real_owner):
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    coordinator, maintenance, _, native, _, helper = _real_coordinator(real_owner)
    native.rows["orders"] = [_order(TradingDay=helper.TODAY, ExchangeID="UNKNOWN")]
    assert coordinator.begin()
    _finish(coordinator)
    assert coordinator.candidate() is None
    assert coordinator.status().state == "GAP" and coordinator.status().pin_retained
    assert maintenance.observed_order_source_status().status == "GAP"
    assert coordinator.close()


def test_real_tracker_waits_for_buffer_receipt_then_saves_candidate(real_owner, monkeypatch):
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    coordinator, maintenance, _, native, journal, helper = _real_coordinator(real_owner)

    def submit(_field, request_id):
        native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
        native.client._handle_query_callback("orders", None, None, request_id, True)
        return 0

    monkeypatch.setattr(native, "ReqQryOrder", submit)
    assert coordinator.begin() and coordinator.advance()
    assert coordinator.status().state == "WAITING_RECEIPTS"
    assert coordinator.advance() and coordinator.candidate() is None
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM observed_order_source_cuts").fetchone()[0] == 0
    maintenance.poll()
    _finish(coordinator)
    candidate = coordinator.candidate()
    assert candidate is not None and candidate.source_prefix >= candidate.end_sequence == 1
    assert coordinator.close()


def test_real_tracker_verifies_at_most_one_receipt_per_advance(real_owner, monkeypatch):
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    coordinator, maintenance, _, native, journal, helper = _real_coordinator(real_owner)

    def submit(_field, request_id):
        for index in range(3):
            native.client._spi.OnRtnOrder(SimpleNamespace(**_order(
                TradingDay=helper.TODAY, OrderRef=str(index + 1), OrderSysID=f"sys-{index + 1}"
            )))
        native.client._handle_query_callback("orders", None, None, request_id, True)
        return 0

    monkeypatch.setattr(native, "ReqQryOrder", submit)
    assert coordinator.begin() and coordinator.advance()
    maintenance.poll()
    original = journal.verify_observed_order_receipt
    calls = []

    def verify(*args, **kwargs):
        calls.append(kwargs["source_sequence"])
        return original(*args, **kwargs)

    monkeypatch.setattr(journal, "verify_observed_order_receipt", verify)
    for expected in (1, 2, 3):
        before = len(calls)
        assert coordinator.advance()
        assert len(calls) - before == 1 and calls[-1] == expected
        assert coordinator.candidate() is None
    _finish(coordinator)
    assert coordinator.candidate() is not None and calls == [1, 2, 3]
    assert coordinator.close()


def test_gap_between_receipt_steps_blocks_candidate_and_retains_pin(real_owner, monkeypatch):
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    coordinator, maintenance, _, native, journal, helper = _real_coordinator(real_owner)

    def submit(_field, request_id):
        for index in range(2):
            native.client._spi.OnRtnOrder(SimpleNamespace(**_order(
                TradingDay=helper.TODAY, OrderRef=str(index + 1), OrderSysID=f"sys-{index + 1}"
            )))
        native.client._handle_query_callback("orders", None, None, request_id, True)
        return 0

    monkeypatch.setattr(native, "ReqQryOrder", submit)
    assert coordinator.begin() and coordinator.advance()
    maintenance.poll()
    assert coordinator.advance() and coordinator.status().state == "VERIFYING_RECEIPTS"
    maintenance._observed_order_source.invalidate()
    assert not coordinator.advance()
    assert coordinator.status().state == "GAP" and coordinator.status().pin_retained
    assert coordinator.candidate() is None
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM observed_order_source_cuts").fetchone()[0] == 0
    assert coordinator.close()


@pytest.mark.parametrize("with_row", [False, True])
def test_busy_after_real_journal_write_marks_gap_and_retains_pin(real_owner, monkeypatch, with_row):
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order

    coordinator, maintenance, _, native, _, helper = _real_coordinator(real_owner)
    native.rows["orders"] = [_order(TradingDay=helper.TODAY)] if with_row else []
    assert coordinator.begin() and coordinator.advance()
    if not with_row:
        assert coordinator.advance() and coordinator.status().state == "COMMITTING"
    original = coordinator._current_cut
    checks = 0

    def busy_after_write():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise _CutBusyError
        return original()

    monkeypatch.setattr(coordinator, "_current_cut", busy_after_write)
    assert not coordinator.advance()
    assert coordinator.status().state == "GAP" and coordinator.status().pin_retained
    assert maintenance.observed_order_source_status().status == "GAP"
    assert coordinator.candidate() is None
    assert coordinator.close()


def test_expired_candidate_is_invalidated_durably(real_owner):
    coordinator, maintenance, _, _, _, _ = _real_coordinator(real_owner)
    assert coordinator.begin()
    _finish(coordinator)
    assert coordinator.candidate() is not None
    coordinator._deadline = 0.0
    assert coordinator.candidate() is None
    assert coordinator.status().state == "GAP"
    assert maintenance.observed_order_source_status().status == "GAP"
    assert coordinator.close()


def test_candidate_reprojects_new_account_gap_without_changing_persisted_cut(real_owner):
    coordinator, maintenance, _, _, journal, helper = _real_coordinator(real_owner)
    assert coordinator.begin()
    _finish(coordinator)
    original = coordinator.candidate()
    assert original is not None and original.source_history_has_gap is False
    guard = maintenance._ledger._guard
    journal.begin_observed_order_source(
        guard, source_ref="other-source", session_ref="other-session", trading_day=helper.TODAY
    )
    journal.invalidate_observed_order_source(
        guard, source_ref="other-source", session_ref="other-session"
    )
    assert maintenance.observed_order_source_status().source_history_has_gap is True
    current = coordinator.candidate()
    assert current is not None and current.source_history_has_gap is True
    assert original.source_history_has_gap is False
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM observed_order_source_cuts").fetchone()[0] == 1
    assert coordinator.close()


def test_candidate_rejects_expired_native_evidence(real_owner, monkeypatch):
    import bt_api_py.ctp_gateway_order_cut as module

    coordinator, maintenance, _, _, _, _ = _real_coordinator(real_owner)
    assert coordinator.begin()
    _finish(coordinator)
    assert coordinator.candidate() is not None
    expires = coordinator._result.query_source.trusted_expires_monotonic
    coordinator._deadline = expires + 100.0
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: expires + 1.0))
    assert coordinator.candidate() is None
    assert coordinator.status().state == "GAP"
    assert maintenance.observed_order_source_status().status == "GAP"
    assert coordinator.close()
