"""Iteration 60 (F5, user decision 2026-11-03): subscription-fed reconciliation.

``StreamedAccountPositions`` is evidence only while provably complete and
live; the strategy ledger reconciles against its ``report()`` with no I/O.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from bt_api_py.account_position_stream import (
    AccountPositionStreamError,
    GatewayAccountPositionStream,
    StreamedAccountPositions,
    gateway_position_reducers,
)
from bt_api_py.strategy_ledger import InstrumentTerms, LedgerIntent, SqliteStrategyLedger

ACCOUNT = "acct-stream"
X = "SIM:X"
NOW = 1_800_000_000_000_000_000


class _Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def ledger(tmp_path, clock):
    item = SqliteStrategyLedger(
        tmp_path / "ledger.sqlite3",
        account_ref=ACCOUNT,
        instruments={X: InstrumentTerms(Decimal("1"), "CNY", "spot")},
        require_budget=False,
        clock_ns=clock,
    )
    item.reserve(LedgerIntent("a", "A", X, "BUY", Decimal("2"), Decimal("100")))
    item.apply_execution("a", cumulative_quantity=2, average_price=100)
    try:
        yield item
    finally:
        item.close()


def test_no_snapshot_is_not_evidence(ledger, clock):
    stream = StreamedAccountPositions(ACCOUNT, "provider:ws", clock_ns=clock)
    report = stream.report()
    assert report.complete is False
    assert ledger.reconcile(report).reason == "REPORT_INCOMPLETE"


def test_snapshot_then_updates_reconcile_without_io(ledger, clock):
    stream = StreamedAccountPositions(ACCOUNT, "provider:ws", clock_ns=clock)
    stream.apply_snapshot({(X, "net"): "2"})
    assert ledger.reconcile(stream.report()).state == "BALANCED"
    clock.now += 1_000
    stream.apply_position(X, "net", "5")  # someone else traded the account
    assert ledger.reconcile(stream.report()).state == "RESIDUAL"
    stream.remove_position(X, "net")
    assert stream.report().positions == {}


def test_long_and_short_rows_net_out(clock):
    stream = StreamedAccountPositions(ACCOUNT, "provider:ws", clock_ns=clock)
    stream.apply_snapshot({(X, "long"): "3", (X, "short"): "1"})
    assert stream.report().positions == {X: Decimal("2")}


def test_interruption_and_sequence_gap_make_it_incomplete(ledger, clock):
    stream = StreamedAccountPositions(ACCOUNT, "provider:ws", clock_ns=clock)
    stream.apply_snapshot({(X, "net"): "2"}, sequence=10)
    assert stream.apply_position(X, "net", "2", sequence=11) is True
    assert stream.apply_position(X, "net", "9", sequence=11) is False  # duplicate ignored
    assert stream.apply_position(X, "net", "9", sequence=5) is False  # stale ignored
    assert stream.report().positions == {X: Decimal("2")} and stream.report().complete
    stream.apply_position(X, "net", "2", sequence=13)  # 12 was lost
    assert stream.report().complete is False and stream.interruption == "SEQUENCE_GAP"
    stream.apply_snapshot({(X, "net"): "2"})  # re-baseline
    assert ledger.reconcile(stream.report()).state == "BALANCED"
    stream.mark_interrupted("DISCONNECTED")
    assert ledger.reconcile(stream.report()).reason == "REPORT_INCOMPLETE"


def test_unwatermarked_snapshot_never_proves_completeness(ledger, clock):
    stream = StreamedAccountPositions(ACCOUNT, "provider:ws", clock_ns=clock)
    revision = stream.snapshot_revision()
    assert stream.apply_unverified_snapshot({(X, "net"): "2"}, observed_ns=NOW, revision=revision)
    assert stream.report().positions == {X: Decimal("2")}
    assert stream.interruption == "BASELINE_CONTINUITY_UNPROVEN"
    assert ledger.reconcile(stream.report()).reason == "REPORT_INCOMPLETE"


def test_sequenced_stream_rejects_missing_sequence_and_invalidates_liveness(clock):
    stream = StreamedAccountPositions(ACCOUNT, "provider:ws", clock_ns=clock)
    stream.apply_snapshot({(X, "net"): "2"}, sequence=10)
    stream.confirm_live(valid_until_ns=NOW + 10_000_000_000)
    assert stream.apply_position(X, "net", "9") is False
    assert stream.report().positions == {X: Decimal("2")}
    assert not stream.report().complete
    assert stream.report().valid_until_ns is None
    assert stream.interruption == "SEQUENCE_MISSING"


@pytest.mark.parametrize("intervening", ["position", "disconnect"])
def test_unwatermarked_snapshot_never_overwrites_intervening_event(clock, intervening):
    stream = StreamedAccountPositions(ACCOUNT, "provider:ws", clock_ns=clock)
    revision = stream.snapshot_revision()
    if intervening == "position":
        stream.apply_position(X, "net", "3")
    else:
        stream.mark_interrupted("STREAM_DISCONNECTED")
    assert not stream.apply_unverified_snapshot({}, observed_ns=NOW, revision=revision)
    assert not stream.report().complete
    assert stream.interruption == "BASELINE_STREAM_CHANGED"
    if intervening == "position":
        assert stream.report().positions == {X: Decimal("3")}


def test_liveness_replaces_the_age_check(ledger, clock):
    stream = StreamedAccountPositions(ACCOUNT, "provider:ws", clock_ns=clock)
    stream.apply_snapshot({(X, "net"): "2"})
    clock.now += 60_000_000_000  # a minute without changes
    assert ledger.reconcile(stream.report()).reason == "REPORT_STALE"
    stream.confirm_live(valid_until_ns=clock.now + 5_000_000_000)
    assert ledger.reconcile(stream.report()).state == "BALANCED"  # unchanged, proven live
    clock.now += 6_000_000_000
    assert ledger.reconcile(stream.report()).reason == "REPORT_STALE"


def test_in_flight_excuse_is_bounded_by_the_settle_window(ledger, clock):
    stream = StreamedAccountPositions(ACCOUNT, "provider:ws", clock_ns=clock)
    stream.apply_snapshot({(X, "net"): "2"})
    clock.now += 1_000
    ledger.reserve(LedgerIntent("b", "A", X, "BUY", Decimal("1"), Decimal("100")))
    ledger.apply_execution("b", cumulative_quantity=1, average_price=100)
    stream.confirm_live(valid_until_ns=clock.now + 120_000_000_000)
    assert ledger.reconcile(stream.report()).state == "IN_FLIGHT"
    clock.now += 31_000_000_000  # the provider never showed the fill
    assert ledger.reconcile(stream.report()).state == "RESIDUAL"


def test_invalid_inputs_are_refused(clock):
    stream = StreamedAccountPositions(ACCOUNT, "provider:ws", clock_ns=clock)
    with pytest.raises(AccountPositionStreamError):
        stream.apply_snapshot({(X, "both"): "1"})
    with pytest.raises(AccountPositionStreamError):
        stream.apply_position(X, "net", "nan")
    with pytest.raises(AccountPositionStreamError):
        stream.confirm_live(valid_until_ns=0)


# -- Gateway reducers and stream loop -------------------------------------------------


def _row(instrument_id, quantity, side="net", observed="2026-11-03T01:00:00Z"):
    return {
        "resource": {"product_type": "linear", "margin_mode": "cross"},
        "instrument": {"instrument_id": instrument_id, "product_type": "linear"},
        "side": side,
        "quantity": quantity,
        "observed_at_utc": observed,
    }


def test_gateway_reducers_keep_the_view_positions():
    snapshot_reducer, event_reducer = gateway_position_reducers()
    view = {
        "schema_id": "sdk.account.view",
        "schema_version": 1,
        "view_scope": "view:p",
        "fact_kinds": ["positions"],
        "positions": [_row(X, "2")],
    }
    state = snapshot_reducer({}, {"payload": view})
    delta = {
        "kind": "account.view_delta",
        "payload": {
            "schema_id": "sdk.account.view.delta",
            "view_scope": "view:p",
            "changes": {"positions": {"upsert": [_row("SIM:Y", "1")], "delete": [_row(X, "2")]}},
        },
    }
    state = event_reducer(state, delta)
    assert sorted(row["instrument"]["instrument_id"] for row in state["positions"].values()) == [
        "SIM:Y"
    ]
    with pytest.raises(AccountPositionStreamError):
        snapshot_reducer({}, {"payload": {**view, "fact_kinds": ["balances"]}})


class _FakeConsumer:
    def __init__(self, pages):
        self.pages = list(pages)
        self.closed = False
        self.last_state_status = None
        self.last_page_at_head = False

    def poll(self):
        page = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        if isinstance(page, Exception):
            raise page
        rows, status, at_head = page
        self.last_state_status = status
        self.last_page_at_head = at_head
        return SimpleNamespace(projection={"view_scope": "v", "positions": rows})

    def close(self):
        self.closed = True


def _status(ready=True, fresh=10.0):
    return {"ready": ready, "fresh_for_seconds": fresh, "received_ns": NOW, "session_generation": "g"}


def _drive(consumer, clock):
    stream = GatewayAccountPositionStream(lambda: consumer, account_ref=ACCOUNT, clock_ns=clock)
    for _ in range(len(consumer.pages)):
        stream._poll_once(consumer)
    return stream


def test_gateway_stream_view_is_diagnostic_without_full_coverage_proof(ledger, clock):
    rows = {"k": _row(X, "2")}
    good = _drive(_FakeConsumer([(rows, _status(), True)]), clock)
    report = good.report()
    assert not report.complete and report.positions == {X: Decimal("2")}
    assert report.valid_until_ns is None
    assert good.positions.interruption == "GATEWAY_FULL_COVERAGE_UNPROVEN"
    assert ledger.reconcile(report).reason == "REPORT_INCOMPLETE"
    for page, reason in (
        ((rows, _status(ready=False), True), "GATEWAY_NOT_READY"),
        ((rows, None, True), "GATEWAY_NOT_READY"),
        ((rows, _status(), False), "GATEWAY_STREAM_BEHIND"),
        (RuntimeError("down"), "GATEWAY_POLL_FAILED"),
    ):
        stream = _drive(_FakeConsumer([(rows, _status(), True), page]), clock)
        assert stream.positions.interruption == reason
        assert ledger.reconcile(stream.report()).reason == "REPORT_INCOMPLETE"


def test_gateway_stream_thread_owns_and_closes_its_consumer(clock):
    consumer = _FakeConsumer([({"k": _row(X, "2")}, _status(), True)])
    stream = GatewayAccountPositionStream(
        lambda: consumer, account_ref=ACCOUNT, poll_interval_seconds=0.01, clock_ns=clock
    ).start()
    try:
        assert not stream.report().complete
        assert stream.report().positions == {X: Decimal("2")}
    finally:
        stream.close()
    assert consumer.closed
    assert stream.report().complete is False


def test_gateway_close_during_blocked_poll_cannot_restore_completeness(clock):
    import threading

    entered = threading.Event()
    release = threading.Event()

    class BlockingConsumer(_FakeConsumer):
        def poll(self):
            entered.set()
            assert release.wait(5)
            return super().poll()

    consumer = BlockingConsumer([({"k": _row(X, "2")}, _status(), True)])
    stream = GatewayAccountPositionStream(
        lambda: consumer, account_ref=ACCOUNT, poll_interval_seconds=0.01, clock_ns=clock
    ).start(wait_seconds=0)
    try:
        assert entered.wait(5)
        stream.close(timeout=0)
        assert stream.report().complete is False
        release.set()
        stream._thread.join(5)
        assert not stream._thread.is_alive()
        assert stream.report().complete is False
        assert stream.positions.interruption == "GATEWAY_STREAM_CLOSED"
    finally:
        release.set()
        stream.close()


def test_gateway_poll_failure_rebuilds_consumer(clock):
    import time

    broken = _FakeConsumer([RuntimeError("down")])
    healthy = _FakeConsumer([({"k": _row(X, "2")}, _status(), True)])
    consumers = iter((broken, healthy))
    stream = GatewayAccountPositionStream(
        lambda: next(consumers), account_ref=ACCOUNT, poll_interval_seconds=0.01, clock_ns=clock
    ).start()
    try:
        deadline = time.monotonic() + 5
        while not stream.report().positions and time.monotonic() < deadline:
            time.sleep(0.01)
        assert broken.closed
        assert not stream.report().complete
        assert stream.report().positions == {X: Decimal("2")}
    finally:
        stream.close()


def test_gateway_stream_rebuilds_its_consumer_after_a_factory_failure(clock):
    attempts = []
    consumer = _FakeConsumer([({"k": _row(X, "2")}, _status(), True)])

    def factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("REQUIRED_REGISTRATION_FAILED")
        return consumer

    stream = GatewayAccountPositionStream(
        factory, account_ref=ACCOUNT, poll_interval_seconds=0.01, clock_ns=clock
    ).start()
    try:
        import time

        deadline = time.monotonic() + 5
        while not stream.report().positions and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not stream.report().complete and len(attempts) == 2
        assert stream.report().positions == {X: Decimal("2")}
    finally:
        stream.close()


def test_gateway_stream_rejects_consumer_account_and_view_mismatches(clock):
    row = _row(X, "2")
    consumer = _FakeConsumer([({"k": row}, _status(), True)])
    consumer._account_ref = "another-account"
    stream = _drive(consumer, clock)
    assert stream.positions.interruption == "GATEWAY_ACCOUNT_SCOPE_MISMATCH"
    assert not stream.report().complete

    consumer = _FakeConsumer([({"k": row}, _status(), True)])
    consumer._account_ref = ACCOUNT
    consumer._stable_binding = {"expected_strategy_scope": "another-view"}
    stream = _drive(consumer, clock)
    assert stream.positions.interruption == "GATEWAY_VIEW_SCOPE_MISMATCH"
    assert not stream.report().complete


def test_gateway_stream_refuses_two_instruments_with_same_ledger_name(clock):
    a, b = _row("SAME", "3"), _row("SAME", "7")
    a["instrument"]["venue"] = "EXCHANGE_A"
    b["instrument"]["venue"] = "EXCHANGE_B"
    stream = _drive(_FakeConsumer([({"a": a, "b": b}, _status(), True)]), clock)
    assert stream.positions.interruption == "GATEWAY_INSTRUMENT_NAME_COLLISION"
    assert not stream.report().complete
    assert stream.report().positions == {}


def test_gateway_stream_refuses_distinct_rows_with_same_ledger_key(clock):
    a, b = _row(X, "3"), _row(X, "7")
    b["resource"]["margin_mode"] = "isolated"
    stream = _drive(_FakeConsumer([({"a": a, "b": b}, _status(), True)]), clock)
    assert stream.positions.interruption == "GATEWAY_POSITION_KEY_COLLISION"
    assert not stream.report().complete
    assert stream.report().positions == {}


def test_gateway_empty_authorized_view_does_not_prove_an_empty_account(ledger, clock):
    consumer = _FakeConsumer([({}, _status(), True)])
    consumer._account_ref = ACCOUNT
    consumer._stable_binding = {"expected_strategy_scope": "v"}
    stream = _drive(consumer, clock)
    report = stream.report()
    assert report.positions == {}
    assert not report.complete
    assert report.valid_until_ns is None
    assert stream.positions.interruption == "GATEWAY_FULL_COVERAGE_UNPROVEN"
    assert ledger.reconcile(report).reason == "REPORT_INCOMPLETE"


def test_gateway_poll_revokes_preexisting_unverified_completeness(clock):
    consumer = _FakeConsumer([({"k": _row(X, "2")}, _status(), True)])
    stream = GatewayAccountPositionStream(lambda: consumer, account_ref=ACCOUNT, clock_ns=clock)
    stream.positions.apply_snapshot({(X, "net"): "2"})
    stream.positions.confirm_live(valid_until_ns=NOW + 1_000_000_000)
    assert stream.diagnostic_report().complete
    assert not stream.report().complete  # F4: view rows are never ledger evidence
    stream._poll_once(consumer)
    assert not stream.diagnostic_report().complete
    assert stream.diagnostic_report().valid_until_ns is None
    assert not stream.report().complete


def test_gateway_reducer_rejects_duplicate_position_identity():
    snapshot_reducer, _ = gateway_position_reducers()
    row = _row(X, "2")
    view = {"schema_id": "sdk.account.view", "schema_version": 1,
            "view_scope": "view:p", "fact_kinds": ["positions"], "positions": [row, row]}
    with pytest.raises(AccountPositionStreamError, match="GATEWAY_DUPLICATE_POSITION_ROW"):
        snapshot_reducer({}, {"payload": view})
