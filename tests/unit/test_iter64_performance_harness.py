"""Bounded, synthetic-only checks for the iteration 64 measurement harness."""

from __future__ import annotations

import importlib.util
import multiprocessing
import queue
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "measure_iter64_risk_service.py"
_spec = importlib.util.spec_from_file_location("iter64_measurement_harness", _SCRIPT)
measure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure)


class _FakeRig:
    settle = measure.Rig.settle

    def __init__(self, statuses, *, fail_on=None):
        self.statuses = statuses
        self.fail_on = fail_on
        self.ingests = []

    def gateway_statuses(self, pending, *, timeout_seconds):
        return {order: self.statuses.get(order, "MISSING") for order in pending}

    def ingest(self, key, status, quantity, filled, *, timeout_seconds):
        self.ingests.append((key, status))
        if key == self.fail_on:
            raise TimeoutError("lost response")

    def ingest_trade(self, index, key, *, timeout_seconds):
        self.ingests.append(("c-" + key, "trade"))

    def working(self, **_kwargs):
        return {}

    def permit_evidence(self, **_kwargs):
        return []


def test_settle_uses_one_deadline_for_many_missing_or_non_sent_orders():
    orders = [(0, str(index)) for index in range(300)]
    rig = _FakeRig({orders[0]: "RESERVED", orders[1]: "UNKNOWN"})
    started = time.monotonic()
    result = rig.settle(orders, status_wait_seconds=0.05, wait_seconds=0)
    assert time.monotonic() - started < 1.0
    assert result["unresolved"] == 300
    assert result["gateway_states"] == {"RESERVED": 1, "UNKNOWN": 1, "MISSING": 298}
    assert result["measurement_status"] == "INCOMPLETE"
    assert rig.ingests == []
    assert len(result["order_results"]) == 300
    assert {item["request_key"] for item in result["order_results"]} == {key for _, key in orders}


def test_settle_only_ingests_sent_and_reports_unknown_after_lost_reply():
    sent = (0, "sent")
    missing = (0, "missing")
    rig = _FakeRig({sent: "SENT", missing: "MISSING"})
    result = rig.settle([sent, missing], status_wait_seconds=0.03, wait_seconds=0)
    assert rig.ingests == [("c-sent", "accepted"), ("c-sent", "filled"), ("c-sent", "trade")]
    assert result["synthetic_returns_attributed"] == 1
    assert result["unresolved"] == 1
    assert result["measurement_status"] == "INCOMPLETE"

    failing = _FakeRig({sent: "SENT"}, fail_on="c-sent")
    result = failing.settle([sent], status_wait_seconds=0.03, wait_seconds=0)
    assert failing.ingests == [("c-sent", "accepted")]
    assert result["gateway_states"] == {"INGEST_UNKNOWN": 1}
    assert result["ingest_unknown"] == 1
    assert result["measurement_status"] == "INCOMPLETE"


def test_batch_status_reads_account_and_strategy_and_distinguishes_missing(tmp_path):
    db_path = tmp_path / "journal.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE direct_journal_commands "
                   "(account TEXT, strategy TEXT, request_key TEXT, status TEXT)")
        db.executemany("INSERT INTO direct_journal_commands VALUES (?, ?, ?, ?)", [
            ("acct", "strategy-0", "sent", "SENT"),
            ("other", "strategy-0", "foreign", "SENT"),
            ("acct", "strategy-1", "same-key", "RESERVED"),
        ])
    rig = measure.Rig.__new__(measure.Rig)
    rig.gateway_root = tmp_path
    rig.support = SimpleNamespace(
        ACCOUNT="acct", strategy_identity=lambda index: SimpleNamespace(actor_ref=f"strategy-{index}"))
    orders = [(0, "sent"), (0, "foreign"), (0, "same-key"), (1, "same-key")]
    orders.extend((0, f"absent-{index}") for index in range(410))
    observed = rig.gateway_statuses(orders, timeout_seconds=1)
    assert observed[(0, "sent")] == "SENT"
    assert observed[(1, "same-key")] == "RESERVED"
    assert observed[(0, "same-key")] == "MISSING"
    assert observed[(0, "foreign")] == "MISSING"
    assert observed[(0, "absent-409")] == "MISSING"
    db_path.unlink()
    assert rig.gateway_statuses([(0, "sent")], timeout_seconds=0.1) == {(0, "sent"): "UNKNOWN"}


def test_synthetic_ingest_checks_ack_identity_and_attribution():
    rig = measure.Rig.__new__(measure.Rig)
    rig.controls = queue.Queue()
    rig.messages = object()
    rig.support = SimpleNamespace(next_message=lambda *a, **k: ("ingested", "wrong", "ATTRIBUTED", 1))
    with pytest.raises(RuntimeError, match="not attributed"):
        rig.ingest("ours", "accepted", "1", "0")
    rig.support.next_message = lambda *a, **k: ("ingested", "ours", "UNATTRIBUTED", 1)
    with pytest.raises(RuntimeError, match="not attributed"):
        rig.ingest("ours", "accepted", "1", "0")


def test_trade_fact_uses_stable_original_binding_and_legacy_order_is_unchanged():
    from bt_api_base.direct_write_protocol import DirectExecutionEventKind

    from tests.integration import _iter64_recovery_support as support

    order = support.synthetic_return_fact(("client", "filled", "1", "1"), support.instrument())
    assert order.kind is DirectExecutionEventKind.ORDER
    assert order.identity_keys == order.binding_keys == ("native-client",)
    trade_input = ("trade", "client", "trade-identity", "1", "10")
    trade = support.synthetic_return_fact(trade_input, support.instrument())
    assert trade.kind is DirectExecutionEventKind.TRADE
    assert trade.identity.client_order_id == "client"
    assert trade.identity_keys == trade.binding_keys == ("native-client",)
    assert trade.venue_key == "native-client"
    assert trade.trade_key == (support.TRADING_DAY, "trade-identity")
    assert trade == support.synthetic_return_fact(trade_input, support.instrument())


def test_trade_not_emitted_for_unknown_command_and_ack_must_match():
    rig = measure.Rig.__new__(measure.Rig)
    rig.controls = queue.Queue()
    rig.messages = object()
    rig.gateway_statuses = lambda orders, **_k: {orders[0]: "UNKNOWN"}
    with pytest.raises(RuntimeError, match="not observed SENT"):
        rig.ingest_trade(0, "key")
    assert rig.controls.empty()
    rig.gateway_statuses = lambda orders, **_k: {orders[0]: "SENT"}
    rig.support = SimpleNamespace(next_message=lambda *_a, **_k: (
        "ingested", "c-key", "ATTRIBUTED", 1, "trade", "wrong-trade"))
    with pytest.raises(RuntimeError, match="not attributed"):
        rig.ingest_trade(0, "key", trade_id="expected")


@pytest.mark.parametrize("state", ["UNKNOWN", "RESERVING", "RESERVED", "RELEASING", "NEW_STATE"])
def test_unfinished_permit_never_makes_settlement_observed(state):
    rig = _FakeRig({})
    rig.working = lambda **_kwargs: {state: 1, "TERMINAL": 3}
    result = rig.settle([], wait_seconds=0, status_wait_seconds=0)
    assert result["measurement_status"] == "INCOMPLETE"
    assert result["unresolved_permit_states"] == {state: 1}


def test_terminal_permit_with_missing_trade_remains_incomplete(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    database = state_dir / "risk.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE risk_pretrade_permits "
                   "(strategy_ref TEXT, request_key TEXT, state TEXT, quantity TEXT, "
                   "order_filled TEXT, trades_quantity TEXT)")
        db.execute("INSERT INTO risk_pretrade_permits VALUES (?, ?, ?, ?, ?, ?)",
                   ("strategy-0", "original", "TERMINAL", "10", "10", "3"))
    rig = measure.Rig.__new__(measure.Rig)
    rig.risk_root = tmp_path
    rig.support = SimpleNamespace(strategy_identity=lambda index: SimpleNamespace(actor_ref=f"strategy-{index}"))
    result = rig.settle([], wait_seconds=0, status_wait_seconds=0,
                        required_risk_orders=[(0, "original")])
    assert result["measurement_status"] == "INCOMPLETE"
    assert result["unresolved_permits"][0]["pending_trade_quantity"] == "7"
    assert result["required_risk_orders"][0]["request_key"] == "original"
    with sqlite3.connect(database) as db:
        db.execute("UPDATE risk_pretrade_permits SET quantity='1', order_filled='1', trades_quantity='1'")
    result = rig.settle([], wait_seconds=0, status_wait_seconds=0,
                        required_risk_orders=[(0, "original")])
    assert result["measurement_status"] == "OBSERVED"
    assert result["required_risk_orders"][0]["resolved"] is True
    missing = rig.settle([], wait_seconds=0, status_wait_seconds=0,
                         required_risk_orders=[(0, "missing")])
    assert missing["measurement_status"] == "INCOMPLETE"
    assert missing["required_risk_orders"][0]["permits"] == []
    with sqlite3.connect(database) as db:
        db.execute("UPDATE risk_pretrade_permits SET quantity='10', order_filled='3', trades_quantity='10'")
    inconsistent = rig.settle([], wait_seconds=0, status_wait_seconds=0,
                              required_risk_orders=[(0, "original")])
    assert inconsistent["measurement_status"] == "INCOMPLETE"
    with sqlite3.connect(database) as db:
        db.execute("UPDATE risk_pretrade_permits SET state='RELEASED', order_filled='1', trades_quantity='1'")
    contradictory_release = rig.settle([], wait_seconds=0, status_wait_seconds=0,
                                       required_risk_orders=[(0, "original")])
    assert contradictory_release["measurement_status"] == "INCOMPLETE"


def test_bounded_wait_observes_late_trade_without_accepting_missing_trade(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    database = state_dir / "risk.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE risk_pretrade_permits "
                   "(strategy_ref TEXT, request_key TEXT, state TEXT, quantity TEXT, "
                   "order_filled TEXT, trades_quantity TEXT)")
        db.execute("INSERT INTO risk_pretrade_permits VALUES (?, ?, ?, ?, ?, ?)",
                   ("strategy-0", "original", "TERMINAL", "1", "1", "0"))
    rig = measure.Rig.__new__(measure.Rig)
    rig.risk_root = tmp_path
    rig.support = SimpleNamespace(strategy_identity=lambda index: SimpleNamespace(actor_ref=f"strategy-{index}"))
    started = time.monotonic()
    timed_out = rig.settle([], wait_seconds=0.05, status_wait_seconds=0,
                           required_risk_orders=[(0, "original")])
    assert time.monotonic() - started < 1
    assert timed_out["measurement_status"] == "INCOMPLETE"
    assert timed_out["required_risk_orders"][0]["resolved"] is False

    def deliver_trade():
        time.sleep(0.05)
        with sqlite3.connect(database) as db:
            db.execute("UPDATE risk_pretrade_permits SET trades_quantity='1'")

    worker = threading.Thread(target=deliver_trade)
    worker.start()
    try:
        settled = rig.settle([], wait_seconds=1, status_wait_seconds=0,
                             required_risk_orders=[(0, "original")])
    finally:
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert settled["measurement_status"] == "OBSERVED"
    assert settled["required_risk_orders"][0]["resolved"] is True


@pytest.mark.parametrize(("state", "quantity", "filled", "trades", "expected"), [
    ("TERMINAL", "1", "0", "0", "INCOMPLETE"),
    ("DENIED", "1", "0", "0", "INCOMPLETE"),
    ("RELEASED", "1", "0", "0", "INCOMPLETE"),
    ("TERMINAL", "2", "2", "2", "INCOMPLETE"),
    ("TERMINAL", "2", "1", "1", "INCOMPLETE"),
    ("TERMINAL", "1", "1", "0", "INCOMPLETE"),
    ("TERMINAL", "1", "1", "1", "OBSERVED"),
])
def test_required_one_lot_must_be_filled_for_the_original_permit(
    tmp_path, state, quantity, filled, trades, expected
):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    with sqlite3.connect(state_dir / "risk.sqlite") as db:
        db.execute("CREATE TABLE risk_pretrade_permits "
                   "(strategy_ref TEXT, request_key TEXT, state TEXT, quantity TEXT, "
                   "order_filled TEXT, trades_quantity TEXT)")
        db.execute("INSERT INTO risk_pretrade_permits VALUES (?, ?, ?, ?, ?, ?)",
                   ("strategy-0", "original", state, quantity, filled, trades))
    rig = measure.Rig.__new__(measure.Rig)
    rig.risk_root = tmp_path
    rig.support = SimpleNamespace(strategy_identity=lambda index: SimpleNamespace(actor_ref=f"strategy-{index}"))
    result = rig.settle([], wait_seconds=0, status_wait_seconds=0,
                        required_risk_orders=[(0, "original")])
    assert result["measurement_status"] == expected
    assert result["required_risk_orders"][0]["resolved"] is (expected == "OBSERVED")
    assert result["required_risk_orders"][0]["expected_quantity"] == "1"


@pytest.mark.integration
def test_loopback_trade_dedup_binding_and_pending_fill(tmp_path):
    """Real loopback Gateway/Risk processes, simulated native sends and returns."""
    from tests.integration import _iter64_recovery_support as support

    rig = measure.Rig(tmp_path, 1, observation_poll="0.05", ttl_seconds=30)
    try:
        rig.start_risk()
        rig.prime()
        _r, _d, answer, key = rig.risk_order(0, "trade-test")
        assert answer.outcome == "DISPATCHED", (answer.outcome, answer.code)
        assert support.until(lambda: rig.gateway_status(0, key), lambda state: state == "SENT",
                             seconds=20) == "SENT"
        rig.ingest("c-" + key, "accepted", "1", "0")
        rig.ingest("c-" + key, "filled", "1", "1")
        assert support.until(lambda: rig.risk_state(0, key), lambda state: state == "TERMINAL",
                             seconds=20) == "TERMINAL"
        pending = rig.settle([], wait_seconds=0, status_wait_seconds=0,
                             required_risk_orders=[(0, key)])
        assert pending["measurement_status"] == "INCOMPLETE"
        assert pending["required_risk_orders"][0]["permits"][0]["pending_trade_quantity"] == "1"

        trade_id = "stable-trade-1"
        rig.ingest_trade(0, key, trade_id=trade_id)
        rig.ingest_trade(0, key, trade_id=trade_id)  # same original fact is idempotent
        settled = rig.settle([], wait_seconds=20, status_wait_seconds=0,
                             required_risk_orders=[(0, key)])
        assert settled["measurement_status"] == "OBSERVED"
        permit = settled["required_risk_orders"][0]["permits"][0]
        assert permit["order_filled"] == permit["trades_quantity"] == "1"

        _r, _d, automatic_answer, automatic_key = rig.risk_order(0, "trade-test")
        assert automatic_answer.outcome == "DISPATCHED", (automatic_answer.outcome, automatic_answer.code)
        automatic = rig.settle([(0, automatic_key)], wait_seconds=20, status_wait_seconds=10,
                               required_risk_orders=[(0, automatic_key)])
        assert automatic["measurement_status"] == "OBSERVED"
        assert automatic["order_results"][0]["gateway_observation"] == "RETURNS_ATTRIBUTED"
        automatic_permit = automatic["required_risk_orders"][0]["permits"][0]
        assert automatic_permit["state"] == "TERMINAL"
        assert automatic_permit["order_filled"] == automatic_permit["trades_quantity"] == "1"

        _r, _d, second, second_key = rig.risk_order(0, "trade-test")
        assert second.outcome == "DISPATCHED", (second.outcome, second.code)
        assert support.until(lambda: rig.gateway_status(0, second_key),
                             lambda state: state == "SENT", seconds=20) == "SENT"
        rig.ingest("c-" + second_key, "accepted", "1", "0")
        # Reusing the first trade key for another SENT order is a conflict,
        # not a fill of the second order.
        with pytest.raises(RuntimeError, match="not attributed"):
            rig.ingest_trade(0, second_key, trade_id=trade_id)
        second_permit = next(item for item in rig.permit_evidence()
                             if item["request_key"] == second_key)
        assert second_permit["trades_quantity"] == "0"
        rig.ingest("c-" + second_key, "filled", "1", "1")
        rig.ingest_trade(0, second_key)
        completed = rig.settle([], wait_seconds=20, status_wait_seconds=0,
                               required_risk_orders=[(0, key), (0, second_key)])
        assert completed["measurement_status"] == "OBSERVED"
    finally:
        rig.close()


def test_stop_risk_rejects_nonzero_exit():
    rig = measure.Rig.__new__(measure.Rig)
    rig.risk = object()
    rig.clients = []
    rig.operator = None
    rig.support = SimpleNamespace(stop_risk=lambda _child: 7)
    with pytest.raises(measure.MeasurementResourceError, match="risk_host_stop_not_clean"):
        rig.stop_risk()
    assert rig.risk_stop_observations == [{
        "operation": "stop", "result": "observed_exit", "exit_code": 7,
        "after_forced_kill": False,
    }]


@pytest.mark.parametrize(
    "value,result,exit_code",
    [
        (0, "observed_exit", 0),
        (7, "observed_exit", 7),
        (-9, "observed_exit", -9),
        (None, "timeout_forced_kill", None),
        (True, "unknown", None),
        (False, "unknown", None),
        (256, "unknown", None),
        (-256, "unknown", None),
        ("7", "unknown", None),
    ],
)
def test_risk_stop_records_only_bounded_exact_integer_or_timeout(value, result, exit_code):
    assert measure._risk_stop_observation(value, operation="stop") == {
        "operation": "stop", "result": result, "exit_code": exit_code,
        "after_forced_kill": False,
    }


def test_timeout_then_cleanup_preserves_both_observations():
    rig = measure.Rig.__new__(measure.Rig)
    rig.clients, rig.operator, rig.sessions = [], None, []
    rig.risk, rig.gateway = object(), None
    returns = iter((None, -9))
    rig.support = SimpleNamespace(stop_risk=lambda _child: next(returns))
    with pytest.raises(measure.MeasurementResourceError):
        rig.stop_risk()
    with pytest.raises(measure.MeasurementCleanupError):
        rig.close()
    assert rig.risk_stop_observations == [
        {"operation": "stop", "result": "timeout_forced_kill", "exit_code": None,
         "after_forced_kill": False},
        {"operation": "cleanup", "result": "observed_exit", "exit_code": -9,
         "after_forced_kill": True},
    ]


def test_boolean_stop_result_is_unknown_and_never_clean():
    rig = measure.Rig.__new__(measure.Rig)
    rig.clients, rig.operator, rig.risk = [], None, object()
    rig.support = SimpleNamespace(stop_risk=lambda _child: False)
    with pytest.raises(measure.MeasurementResourceError):
        rig.stop_risk()
    assert rig.risk_stop_observations[0]["result"] == "unknown"
    assert rig.risk_stop_observations[0]["exit_code"] is None


def test_successful_stop_records_observed_zero():
    rig = measure.Rig.__new__(measure.Rig)
    rig.clients, rig.operator, rig.risk = [], None, object()
    rig.support = SimpleNamespace(stop_risk=lambda _child: 0)
    rig.stop_risk()
    assert rig.risk is None
    assert rig.risk_stop_observations == [{
        "operation": "stop", "result": "observed_exit", "exit_code": 0,
        "after_forced_kill": False,
    }]


def test_stop_exception_records_unknown_without_exception_text():
    rig = measure.Rig.__new__(measure.Rig)
    rig.clients, rig.operator, rig.risk = [], None, object()

    def fail(_child):
        raise RuntimeError("private error text")

    rig.support = SimpleNamespace(stop_risk=fail)
    with pytest.raises(RuntimeError):
        rig.stop_risk()
    assert rig.risk_stop_observations[0]["result"] == "unknown"
    assert rig.risk_stop_observations[0]["exit_code"] is None
    assert "private" not in str(rig.risk_stop_observations)


def test_cleanup_reports_fixed_resource_categories_without_exception_text():
    rig = measure.Rig.__new__(measure.Rig)
    rig.clients, rig.operator, rig.sessions = [], None, []
    rig.risk = object()
    rig.gateway = None
    rig.support = SimpleNamespace(stop_risk=lambda _child: 7)
    with pytest.raises(measure.MeasurementCleanupError) as error:
        rig.close()
    assert measure._cleanup_result(error.value) == {
        "status": "INCOMPLETE", "categories": ["risk_host_stop_not_clean"]
    }


def test_claim_worker_records_warmup_identity(monkeypatch):
    from tests.integration import _iter64_recovery_support as support

    class Client:
        def start(self, **_kwargs):
            pass

        def reserve(self, *_args, **_kwargs):
            answer = SimpleNamespace(outcome="RESERVED", permit_id="p", reservation_id="r",
                                     view_digest="digest")
            return SimpleNamespace(outcome="ANSWERED", answer=answer)

        def dispatch(self, *_args, **_kwargs):
            return SimpleNamespace(outcome="ANSWERED", answer=SimpleNamespace(outcome="DISPATCHED"))

        def close(self, **_kwargs):
            pass

    monkeypatch.setattr(support, "strategy_session", lambda *a, **k: Client())
    monkeypatch.setattr(support, "risk_client", lambda *a, **k: Client())
    monkeypatch.setattr(support, "order_request", lambda *a, **k: object())
    ready, results = queue.Queue(), queue.Queue()
    event = multiprocessing.Event()
    event.set()
    measure._claim_worker(0, "unused", "unused", {}, "journal", 1, ready, event, results)
    result = results.get_nowait()
    assert result["warmup_outcome"] == "DISPATCHED"
    assert result["warmup_key"] is not None
    assert len(result["keys"]) == 1
    assert result["warmup_key"] not in result["keys"]

    calls = []

    class UnknownClient(Client):
        def reserve(self, *_args, **kwargs):
            calls.append(kwargs["request_key"])
            return SimpleNamespace(outcome="UNKNOWN", answer=None)

    monkeypatch.setattr(support, "risk_client", lambda *a, **k: UnknownClient())
    ready, results = queue.Queue(), queue.Queue()
    measure._claim_worker(0, "unused", "unused", {}, "journal", 3, ready, event, results)
    result = results.get_nowait()
    assert len(calls) == 1
    assert result["reserve_attempts"] == [{"request_key": calls[0], "outcome": "UNKNOWN"}]
    assert result["warmup_key"] is None


def test_fresh_risk_retains_unknown_warmup_identity_and_stops_new_attempts():
    rig = measure.Rig.__new__(measure.Rig)
    rig.strategy_count = 2
    rig.clients = [object(), object()]
    rig.stop_risk = lambda: None
    rig.start_risk = lambda **_kwargs: None
    rig.wait_verified = lambda _seconds: 1
    rig.key = lambda _prefix: "warm-original"
    called = []

    def unavailable(*_args, **kwargs):
        called.append(kwargs["key"])
        raise TimeoutError()

    rig.risk_order = unavailable
    rig.settle = lambda orders, **_kwargs: {"measurement_status": "INCOMPLETE", "orders": orders}
    result = measure._guard(rig.fresh_risk)
    assert result["measurement_status"] == "INCOMPLETE"
    assert called == ["warm-original"]
    assert result["evidence"]["warmups"][0]["request_key"] == "warm-original"
    assert result["evidence"]["warmups"][0]["outcome"] == "UNKNOWN:TimeoutError"


def test_recovery_keeps_unknown_original_identity_without_sending_another():
    class FakeRig:
        called = []

        def key(self, _prefix):
            return "original"

        def admin(self, *_args, **_kwargs):
            return {"summary": {"observation": {"continuity": "VERIFIED"}}}

        def risk_order(self, *_args, **kwargs):
            self.called.append(kwargs["key"])
            return 1, None, None, kwargs["key"]

    rig = FakeRig()
    verified, dispatched, attempts = measure._recovery(rig, 2)
    assert verified is not None and dispatched is None
    assert rig.called == ["original"]
    assert attempts == {"original": "NONE"}


def test_gateway_restart_rejects_unknown_redispatch_even_when_other_checks_pass(monkeypatch):
    class Client:
        def reserve(self, *_args, **_kwargs):
            answer = SimpleNamespace(outcome="RESERVED", permit_id="p", reservation_id="r",
                                     view_digest="digest")
            return SimpleNamespace(outcome="ANSWERED", answer=answer)

        def dispatch(self, *_args, **_kwargs):
            return SimpleNamespace(outcome="UNKNOWN")

    class FakeRig:
        support = SimpleNamespace(order_request=lambda *a: object(),
                                  until=lambda read, _done: read())
        clients = [Client()]
        count = 0
        seen = {}

        def key(self, prefix):
            self.count += 1
            return prefix + str(self.count)

        def risk_order(self, *_args, **kwargs):
            return 1, 1, SimpleNamespace(outcome="DISPATCHED"), kwargs["key"]

        def gateway_status(self, *_args):
            return "SENT"

        def risk_state(self, _index, key):
            count = self.seen.get(key, 0)
            self.seen[key] = count + 1
            return ("DISPATCHED" if key.startswith("gwr-sent") else "RESERVED") if count == 0 else "TERMINAL"

        def stop_gateway(self):
            return 0.1

        def start_gateway(self):
            return 0.1

        def open_sessions(self):
            pass

        def settle(self, orders, **_kwargs):
            assert len(orders) in (0, 2)  # old reserved key must also be settled
            return {"measurement_status": "OBSERVED"}

        def stop_risk(self):
            pass

        def start_risk(self):
            return 0.1

    monkeypatch.setattr(measure, "_recovery", lambda *_a: (0.1, 0.2, {}))
    monkeypatch.setattr(measure.time, "sleep", lambda _s: None)
    result = measure.phase_gateway_restart(FakeRig(), 1, 1)
    assert result["measurement_status"] == "INCOMPLETE"
    assert result["reserved_order_evidence"][0]["dispatch_outcome"] == "UNKNOWN"
    assert result["reserved_order_evidence"][0]["request_key"].startswith("gwr-reserved")


def _sleep_worker():
    time.sleep(30)


def test_cleanup_only_terminates_explicit_owned_workers(monkeypatch):
    context = multiprocessing.get_context("spawn")
    owned, unrelated = context.Process(target=_sleep_worker), context.Process(target=_sleep_worker)
    owned.start()
    unrelated.start()
    original = measure._join_workers
    monkeypatch.setattr(measure, "_join_workers", lambda workers, _timeout: original(workers, 0.1))
    try:
        remaining, forced = measure._cleanup_workers([owned])
        assert forced == 1
        assert remaining == []
        assert not owned.is_alive()
        assert unrelated.is_alive()
    finally:
        if unrelated.is_alive():
            unrelated.terminate()
        unrelated.join(5)


def test_queue_timeout_returns_partial_and_failed_phase_is_not_success():
    messages = queue.Queue()
    messages.put("one")
    start = time.monotonic()
    assert measure._queue_until(messages, 300, 0.05) == ["one"]
    assert time.monotonic() - start < 1.0
    failed = measure._guard(lambda: (_ for _ in ()).throw(TimeoutError()))
    assert failed["phase_failed"] == "TimeoutError"
    assert measure._incomplete({"phases": [failed]})
    assert not measure._incomplete({"measurement_status": "OBSERVED"})


def test_gateway_restart_failure_keeps_fixed_substage_and_no_exception_message(monkeypatch):
    class FakeRig:
        clients = []
        support = SimpleNamespace(until=lambda read, _done: read())

        def stop_gateway(self):
            error = RuntimeError("secret exception text")
            error.code = "secret exception code"
            raise error

    progress = {}
    result = measure._guard(
        lambda: measure.phase_gateway_restart(
            FakeRig(), 0, 1, checkpoint=lambda value: progress.update(stage=value)
        ),
        substage=lambda: progress["stage"],
    )
    assert result == {"phase_failed": "RuntimeError", "code": "", "substage": "stop_gateway",
                      "measurement_status": "INCOMPLETE"}
    assert "secret" not in str(result)


def test_gateway_restart_preserves_specific_risk_stop_category(monkeypatch):
    class FakeRig:
        clients = []
        support = SimpleNamespace(until=lambda read, _done: read())

        def stop_gateway(self):
            return 0.1

        def start_gateway(self):
            return 0.1

        def open_sessions(self):
            pass

        def settle(self, *_args, **_kwargs):
            return {"measurement_status": "INCOMPLETE"}

        def stop_risk(self):
            raise measure.MeasurementResourceError("risk_host_stop_not_clean")

    monkeypatch.setattr(measure, "_recovery", lambda *_a: (None, None, {}))
    progress = {}
    result = measure._guard(
        lambda: measure.phase_gateway_restart(
            FakeRig(), 0, 1, checkpoint=lambda value: progress.update(stage=value)
        ),
        substage=lambda: progress["stage"],
    )
    assert result["substage"] == "stop_risk"
    assert result["code"] == "risk_host_stop_not_clean"
    assert result["measurement_status"] == "INCOMPLETE"


def test_round_trip_uses_bounded_wait_outside_request_timings():
    class Command:
        def reserve(self, *_args, **_kwargs):
            return SimpleNamespace(record=object())

        def dispatch(self, *_args, **_kwargs):
            return SimpleNamespace(outcome="QUEUED")

    class FakeRig:
        support = SimpleNamespace(order_request=lambda *_args: object())
        sessions = [SimpleNamespace(command_client=Command())]
        calls = []
        sequence = 0

        def key(self, prefix):
            self.sequence += 1
            return prefix + str(self.sequence)

        def working(self):
            return {}

        def permit_evidence(self):
            return []

        def risk_order(self, _index, _prefix, *, key):
            return 1.0, 2.0, SimpleNamespace(outcome="DISPATCHED"), key

        def settle(self, orders, **kwargs):
            self.calls.append((orders, kwargs))
            return {"unresolved": 0, "ingest_unknown": 0, "measurement_status": "OBSERVED",
                    "order_results": [], "gateway_states": {}}

    rig = FakeRig()
    result = measure.phase_round_trips(rig, 1)
    assert result["measurement_status"] == "OBSERVED"
    assert len(rig.calls) == 6  # five warmups, one sample
    assert all(options["wait_seconds"] == measure._SETTLEMENT_WAIT_SECONDS
               and len(options["required_risk_orders"]) == 1 for _, options in rig.calls)
    assert result["settlement_wait_budget_seconds"] == measure._SETTLEMENT_WAIT_SECONDS


def test_missing_worker_result_fails_closed_and_workers_are_reaped(monkeypatch):
    class FakeQueue(queue.Queue):
        def close(self):
            pass

        def cancel_join_thread(self):
            pass

    class FakeProcess:
        started = False
        exitcode = 0

        def start(self):
            self.started = True

        def join(self, _timeout):
            pass

        def is_alive(self):
            return False

    class FakeContext:
        Queue = FakeQueue
        Event = staticmethod(multiprocessing.Event)
        Lock = staticmethod(multiprocessing.Lock)

        def Process(self, **_kwargs):
            return FakeProcess()

    class FakeSession:
        def close(self, **_kwargs):
            pass

    class FakeRig:
        gateway_endpoint = risk_endpoint = journal_id = "unused"
        keys = {}
        sessions = [FakeSession()]
        context = FakeContext()
        _cleanup_unknown = False

        def __init__(self):
            self._workers = []
            self._worker_resources = []

        def working(self):
            return {}

        def permit_evidence(self):
            return []

        def close_sessions(self):
            pass

        def close_risk_clients(self):
            pass

        def open_sessions(self):
            pass

        def settle(self, orders, **_kwargs):
            assert orders == []
            return {"measurement_status": "OBSERVED"}

    rig = FakeRig()
    with pytest.raises(measure.MeasurementCleanupError) as error:
        measure.phase_claim_processes(rig, 2, 2, readiness_timeout=0.01,
                                       result_timeout=0.01)
    # No worker result was observed: the phase fails closed even though both
    # child processes are reaped, because exit is not client cleanup evidence.
    assert error.value.categories == ["worker_cleanup_unknown"]
    assert len(rig._workers) == 2 and all(not worker.is_alive() for worker in rig._workers)
    assert rig._worker_result_evidence["received_count"] == 0
    assert rig._worker_result_evidence["unreceived_or_unverified"] == "UNKNOWN"

    rows = [{"index": i, "first": 1.0, "last": 2.0, "latencies": [10],
             "outcomes": {"DISPATCHED": 1}, "reserve_outcomes": {"RESERVED": 2},
             "keys": [f"order-{i}"], "warmup_key": f"warm-{i}",
             "warmup_outcome": "DISPATCHED", "error": None,
             "admission_status": "RELEASED", "abort_status": "NOT_REQUESTED"}
            for i in range(2)]
    responses = iter(([(0, "READY"), (1, "READY")], rows))

    def collect(_source, _count, _timeout, items=None, _stop=None):
        value = next(responses)
        if items is None:
            return value
        items.extend(value)
        return items

    monkeypatch.setattr(measure, "_queue_until", collect)

    def cleanup(_workers):
        # Simulate explicit reaping while the original process objects and
        # queues stay retained for the process-memory evidence.
        rig._workers.clear()
        return [], 0

    monkeypatch.setattr(measure, "_cleanup_workers", cleanup)
    observed = []

    def settle(orders, **_kwargs):
        observed.extend(orders)
        return {"measurement_status": "OBSERVED"}

    rig.settle = settle
    result = measure.phase_claim_processes(rig, 2, 1, readiness_timeout=0.01,
                                           result_timeout=0.01)
    assert set(observed) == {(0, "warm-0"), (1, "warm-1"), (0, "order-0"), (1, "order-1")}
    assert result["readiness_gate"]["decision"] == "RELEASED"
    assert result["readiness_gate"]["admitted_workers"] == 2
    assert result["measurement_status"] == "OBSERVED"


def test_observation_lag_never_fills_a_non_sent_order():
    class FakeRig:
        support = SimpleNamespace(until=lambda read, done: read())

        def key(self, _prefix):
            return "key"

        def risk_order(self, *_args, **_kwargs):
            return 1, 1, SimpleNamespace(outcome="DISPATCHED"), "key"

        def gateway_status(self, *_args):
            return "RESERVED"

        def ingest(self, *_args):
            raise AssertionError("must not synthesize a return for a non-SENT command")

        def settle(self, orders, **kwargs):
            assert orders == [] and kwargs["required_risk_orders"] == [(0, "key")]
            assert kwargs["wait_seconds"] == measure._SETTLEMENT_WAIT_SECONDS
            return {"measurement_status": "INCOMPLETE"}

    result = measure.phase_observation_lag(FakeRig(), 1)
    assert result["not_observed"] == 1
    assert result["measurement_status"] == "INCOMPLETE"


def test_terminal_lag_observation_does_not_hide_missing_trade():
    class FakeRig:
        support = SimpleNamespace(until=lambda read, done: read())

        def key(self, _prefix):
            return "original"

        def risk_order(self, *_args, **kwargs):
            return 1, 1, SimpleNamespace(outcome="DISPATCHED"), kwargs["key"]

        def gateway_status(self, *_args):
            return "SENT"

        def ingest(self, *_args, **_kwargs):
            return time.time_ns()

        def ingest_trade(self, *_args, **_kwargs):
            return time.time_ns()

        def risk_state(self, *_args, **_kwargs):
            return "TERMINAL"

        def settle(self, _orders, **kwargs):
            assert kwargs["required_risk_orders"] == [(0, "original")]
            assert kwargs["wait_seconds"] == measure._SETTLEMENT_WAIT_SECONDS
            return {"measurement_status": "INCOMPLETE", "pending_trade_quantity": "1"}

    result = measure.phase_observation_lag(FakeRig(), 1)
    assert result["gateway_ingest_to_risk_terminal"]["n"] == 1
    assert result["not_observed"] == 0
    assert result["measurement_status"] == "INCOMPLETE"
    assert "TRADE injection and polling delay" in result["measurement_scope"]


def test_risk_start_timeout_reaps_the_child(monkeypatch, tmp_path):
    class Child:
        def poll(self):
            return None

    child = Child()
    stopped = []
    rig = measure.Rig.__new__(measure.Rig)
    rig.ready_file = tmp_path / "missing"
    rig.config = tmp_path / "config"
    rig.support = SimpleNamespace(SDK_PYTHONPATH="test-only")
    rig.stop_risk = lambda: stopped.append(rig.risk)
    clock = iter((0, 100))
    monkeypatch.setattr(measure, "time", SimpleNamespace(perf_counter=lambda: 0,
                                                          monotonic=lambda: next(clock),
                                                          sleep=lambda _x: None))
    monkeypatch.setattr(measure.subprocess, "Popen", lambda *a, **k: child)
    with pytest.raises(TimeoutError, match="not become ready"):
        rig.start_risk()
    assert stopped == [child]


def test_main_returns_nonzero_and_retains_failures(monkeypatch, tmp_path, capsys):
    class FakeRig:
        def __init__(self, *_args, **_kwargs):
            pass

        def start_risk(self):
            return 0

        def prime(self):
            pass

        def fresh_risk(self, **_kwargs):
            pass

        def close(self):
            pass

    def fail(*_args, **_kwargs):
        raise TimeoutError("test phase")

    monkeypatch.setattr(measure, "Rig", FakeRig)
    for name in ("phase_round_trips", "phase_claim_processes", "phase_observation_lag",
                 "phase_risk_restart", "phase_gateway_restart"):
        monkeypatch.setattr(measure, name, fail)
    assert measure.main(["--out", str(tmp_path), "--orders", "1", "--slow-disk-ms", ""]) == 1
    result = __import__("json").loads((tmp_path / "risk_service_measurements.json").read_text())
    assert result["measurement_status"] == "INCOMPLETE"
    assert result["round_trips"]["phase_failed"] == "TimeoutError"
    assert "INCOMPLETE" in capsys.readouterr().out


def test_gateway_failure_and_cleanup_category_survive_in_partial(monkeypatch, tmp_path):
    class FakeRig:
        def __init__(self, *_args, **_kwargs):
            self.risk_stop_observations = []

        def start_risk(self):
            return 0

        def prime(self):
            pass

        def fresh_risk(self, **_kwargs):
            pass

        def close(self):
            self.risk_stop_observations.append(
                measure._risk_stop_observation(-9, operation="cleanup", after_forced_kill=True)
            )
            raise measure.MeasurementCleanupError(["risk_host_stop_not_clean"])

    def failed_gateway(_rig, _count, _timeout, *, checkpoint):
        checkpoint("stop_risk")
        _rig.risk_stop_observations.append(measure._risk_stop_observation(None, operation="stop"))
        raise measure.MeasurementResourceError("risk_host_stop_not_clean")

    monkeypatch.setattr(measure, "Rig", FakeRig)
    for name in ("phase_round_trips", "phase_claim_processes", "phase_observation_lag",
                 "phase_risk_restart"):
        monkeypatch.setattr(measure, name, lambda *_a, **_k: {"measurement_status": "INCOMPLETE"})
    monkeypatch.setattr(measure, "phase_gateway_restart", failed_gateway)
    with pytest.raises(measure.MeasurementCleanupError):
        measure.main(["--out", str(tmp_path), "--orders", "1", "--slow-disk-ms", ""])
    result = __import__("json").loads((tmp_path / "risk_service_measurements.partial.json").read_text())
    assert result["measurement_status"] == "INCOMPLETE"
    assert result["gateway_restart"]["substage"] == "stop_risk"
    assert result["gateway_restart"]["code"] == "risk_host_stop_not_clean"
    assert result["cleanup"] == {"status": "INCOMPLETE", "categories": ["risk_host_stop_not_clean"]}
    assert result["risk_stop_observations"] == [
        {"operation": "stop", "result": "timeout_forced_kill", "exit_code": None,
         "after_forced_kill": False},
        {"operation": "cleanup", "result": "observed_exit", "exit_code": -9,
         "after_forced_kill": True},
    ]
    assert not (tmp_path / "risk_service_measurements.json").exists()


def test_interrupted_gateway_substage_and_successful_cleanup_are_recorded(monkeypatch, tmp_path):
    class FakeRig:
        def __init__(self, *_args, **_kwargs):
            pass

        def start_risk(self):
            return 0

        def prime(self):
            pass

        def fresh_risk(self, **_kwargs):
            pass

        def close(self):
            pass

    def interrupted_gateway(_rig, _count, _timeout, *, checkpoint):
        checkpoint("start_gateway")
        raise KeyboardInterrupt("secret interruption text")

    monkeypatch.setattr(measure, "Rig", FakeRig)
    for name in ("phase_round_trips", "phase_claim_processes", "phase_observation_lag",
                 "phase_risk_restart"):
        monkeypatch.setattr(measure, name, lambda *_a, **_k: {"measurement_status": "INCOMPLETE"})
    monkeypatch.setattr(measure, "phase_gateway_restart", interrupted_gateway)
    with pytest.raises(KeyboardInterrupt):
        measure.main(["--out", str(tmp_path), "--orders", "1", "--slow-disk-ms", ""])
    raw = (tmp_path / "risk_service_measurements.partial.json").read_text()
    result = __import__("json").loads(raw)
    assert result["measurement_status"] == "INCOMPLETE"
    assert result["active_substage"] == "start_gateway"
    assert result["cleanup"] == {"status": "COMPLETE", "categories": []}
    assert "secret interruption text" not in raw


def test_main_does_not_overwrite_existing_measurement_evidence(tmp_path):
    evidence = tmp_path / "risk_service_measurements.partial.json"
    evidence.write_text("original", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        measure.main(["--out", str(tmp_path)])
    assert error.value.code != 0
    assert evidence.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("option", ["--orders", "--per-client", "--lag-samples"])
def test_main_rejects_empty_sample_counts(tmp_path, option):
    with pytest.raises(SystemExit) as error:
        measure.main(["--out", str(tmp_path), option, "0"])
    assert error.value.code != 0
    assert not (tmp_path / "risk_service_measurements.json").exists()


def test_interrupted_startup_leaves_incomplete_partial(monkeypatch, tmp_path):
    class BrokenRig:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("synthetic startup failure")

    monkeypatch.setattr(measure, "Rig", BrokenRig)
    with pytest.raises(RuntimeError, match="startup failure"):
        measure.main(["--out", str(tmp_path)])
    result = __import__("json").loads((tmp_path / "risk_service_measurements.partial.json").read_text())
    assert result["measurement_status"] == "INCOMPLETE"
    assert result["failure"] == {"stage": "construct", "type": "RuntimeError"}


@pytest.mark.parametrize("failure_stage", ["first_risk_start", "prime", "close"])
def test_lifecycle_failure_and_cleanup_are_recorded_without_masking(monkeypatch, tmp_path, failure_stage):
    class FakeRig:
        def __init__(self, *_args, **_kwargs):
            pass

        def start_risk(self):
            if failure_stage == "first_risk_start":
                raise ValueError("first error")
            return 0

        def prime(self):
            if failure_stage == "prime":
                raise ValueError("first error")

        def fresh_risk(self, **_kwargs):
            pass

        def close(self):
            raise RuntimeError("cleanup error")

    monkeypatch.setattr(measure, "Rig", FakeRig)
    for name in ("phase_round_trips", "phase_claim_processes", "phase_observation_lag",
                 "phase_risk_restart", "phase_gateway_restart"):
        monkeypatch.setattr(measure, name, lambda *_a, **_k: {"phase_failed": "Synthetic"})
    expected = RuntimeError if failure_stage == "close" else ValueError
    with pytest.raises(expected):
        measure.main(["--out", str(tmp_path), "--orders", "1", "--slow-disk-ms", ""])
    result = __import__("json").loads((tmp_path / "risk_service_measurements.partial.json").read_text())
    assert result["measurement_status"] == "INCOMPLETE"
    assert result["failure"] == {"stage": failure_stage, "type": expected.__name__}
    if failure_stage != "close":
        assert result["cleanup_error"] == "RuntimeError"


def test_fresh_risk_failure_is_recorded_per_phase(monkeypatch, tmp_path, capsys):
    class FakeRig:
        def __init__(self, *_args, **_kwargs):
            pass

        def start_risk(self):
            return 0

        def prime(self):
            pass

        def fresh_risk(self, **_kwargs):
            raise measure.MeasurementIncompleteError("warmup", {"request_key": "original"})

        def close(self):
            pass

    monkeypatch.setattr(measure, "Rig", FakeRig)
    monkeypatch.setattr(measure, "phase_round_trips", lambda *_a: {"measurement_status": "OBSERVED"})
    monkeypatch.setattr(measure, "phase_risk_restart", lambda *_a, **_k: {
        "mode": "synthetic", "runs": [], "measurement_status": "INCOMPLETE"})
    assert measure.main(["--out", str(tmp_path), "--orders", "1", "--slow-disk-ms", ""]) == 1
    result = __import__("json").loads((tmp_path / "risk_service_measurements.json").read_text())
    assert result["measurement_status"] == "INCOMPLETE"
    assert all(item["evidence"]["request_key"] == "original" for item in result["claim_throughput"])
    partial = __import__("json").loads((tmp_path / "risk_service_measurements.partial.json").read_text())
    assert partial["measurement_status"] == "INCOMPLETE"
    capsys.readouterr()


def test_constructor_failure_closes_its_started_gateway(monkeypatch, tmp_path):
    from tests.integration import _iter64_recovery_support as support
    from tests.integration import test_service_account_instrument_metadata as metadata

    class FakeGateway:
        pid = 123
        stopped = False

        def is_alive(self):
            return not self.stopped

    gateway = FakeGateway()
    monkeypatch.setattr(support, "keypairs", lambda _n: {})
    monkeypatch.setattr(metadata, "_free_endpoint", lambda: "tcp://127.0.0.1:1")

    def fail_start(rig):
        rig.gateway = gateway
        raise RuntimeError("startup failure")

    monkeypatch.setattr(measure.Rig, "start_gateway", fail_start)
    monkeypatch.setattr(measure.Rig, "stop_gateway", lambda _rig: setattr(gateway, "stopped", True))
    with pytest.raises(RuntimeError, match="startup failure"):
        measure.Rig(tmp_path, 1, observation_poll="0.1", ttl_seconds=20)
    assert gateway.stopped


def test_stats_never_invents_numbers_without_samples():
    assert measure._stats([]) == {"n": 0}
    sampled = measure._stats([1.0, 2.0])
    assert sampled["n"] == 2
    assert sampled["p50_ms"] == 1.0 and sampled["p99_ms"] == 2.0
    assert sampled["max_ms"] == 2.0 and sampled["mean_ms"] == 1.5


@pytest.mark.parametrize(("cleanup", "expected"), [
    ({"status": "COMPLETE", "categories": []}, "OBSERVED"),
    ({"status": "INCOMPLETE", "categories": ["gateway_stop_failed"]}, "INCOMPLETE"),
    ({"status": "UNKNOWN", "categories": ["cleanup_unknown"]}, "INCOMPLETE"),
    (None, "INCOMPLETE"),
])
def test_overall_status_is_incomplete_for_unfinished_cleanup(cleanup, expected):
    result = {"round_trips": {"measurement_status": "OBSERVED"}}
    if cleanup is not None:
        result["cleanup"] = cleanup
    assert measure._overall_measurement_status(result) == expected


def test_overall_status_is_incomplete_for_any_failed_phase():
    assert measure._overall_measurement_status({
        "cleanup": {"status": "COMPLETE", "categories": []},
        "claims": [{"phase_failed": "TimeoutError", "measurement_status": "INCOMPLETE"}],
    }) == "INCOMPLETE"


def test_phase_conditions_carry_environment_and_incomplete_markdown_is_explicit():
    result = {
        "evidence": "INTEGRATION_SIM",
        "machine": {"platform": "fake", "machine": "fake"},
        "config": {"orders": 1},
        "load_average": {},
        "cleanup": {"status": "INCOMPLETE", "categories": ["gateway_stop_failed"]},
        "round_trips": {"measurement_status": "INCOMPLETE",
                        "direct_reserve": {"n": 0}, "direct_dispatch": {"n": 0},
                        "risk_reserve": {"n": 0}, "risk_dispatch": {"n": 0}},
        "claim_throughput": [],
        "observation_lag": {"measurement_status": "INCOMPLETE",
                            "gateway_ingest_to_risk_terminal": {"n": 0}, "not_observed": 3},
        "slow_disk": {}, "risk_restart": [], "gateway_restart": {},
        "measurement_status": "INCOMPLETE",
    }
    block = measure._with_conditions(dict(result["round_trips"]), result,
                                     stage="round_trips", orders=1)
    assert block["conditions"] == {
        "stage": "round_trips",
        "evidence": "INTEGRATION_SIM",
        "machine": result["machine"],
        "config": result["config"],
        "load_average": measure._load(),
        "orders": 1,
    }
    text = measure._markdown(result)
    assert "Cleanup:" in text and "INCOMPLETE" in text
    # A block with no samples shows n=0 and no fabricated percentiles.
    assert "| Direct Gateway reserve | 0 | None | None | None |" in text


def test_main_attaches_conditions_to_every_phase_block(monkeypatch, tmp_path):
    class FakeRig:
        def __init__(self, *_args, **_kwargs):
            pass

        def start_risk(self):
            return 0

        def prime(self):
            pass

        def fresh_risk(self, **_kwargs):
            pass

        def close(self):
            pass

    def failed(*_args, **_kwargs):
        return {"phase_failed": "Synthetic", "measurement_status": "INCOMPLETE"}

    monkeypatch.setattr(measure, "Rig", FakeRig)
    for name in ("phase_round_trips", "phase_claim_processes", "phase_observation_lag",
                 "phase_risk_restart", "phase_gateway_restart"):
        monkeypatch.setattr(measure, name, failed)
    assert measure.main(["--out", str(tmp_path), "--orders", "1", "--per-client", "1",
                         "--lag-samples", "1", "--slow-disk-ms", "0"]) == 1
    result = __import__("json").loads((tmp_path / "risk_service_measurements.json").read_text())
    blocks = [(result["round_trips"], "round_trips")]
    blocks += [(item, "claims") for item in result["claim_throughput"]]
    blocks += [(result["observation_lag"], "observation_lag")]
    blocks += [(item, "risk_restart") for item in result["risk_restart"]]
    blocks += [(result["slow_disk"]["0ms"], "slow_disk")]
    blocks += [(result["gateway_restart"], "gateway_restart")]
    for block, stage in blocks:
        assert block["conditions"]["stage"] == stage
        assert block["conditions"]["evidence"] == result["evidence"]
        assert block["conditions"]["machine"] == result["machine"]
        assert block["conditions"]["config"] == result["config"]
        assert isinstance(block["conditions"]["load_average"], list)
    assert result["measurement_status"] == "INCOMPLETE"
