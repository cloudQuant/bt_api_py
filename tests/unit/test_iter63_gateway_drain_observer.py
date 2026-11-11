"""The owner thread keeps the target states of active DRAIN causes current.

An operator's DRAIN used to be reconciled only on the operator's request: a listing could show a
target as PENDING long after the venue had reported the order cancelled.
"""
import logging
import threading
from dataclasses import replace

import pytest
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from test_iter63_direct_execution_journal import command, fact

from bt_api_py.gateway_drain_observer import GatewayDrainTargetObserver


class World:
    def __init__(self, tmp_path, **settings):
        directory = tmp_path / "guards"
        directory.mkdir()
        self.guard = LocalAccountSessionGuard(directory, "acct", "owner")
        self.journal = GatewayDirectCommandJournal.create(tmp_path / "db", "acct", generation=self.guard.acquire(),
                                                          clock=lambda: 100.0)
        self.control = self.journal.enable_controls(self.guard)
        self.ledger = GatewayDirectExecutionLedger(self.journal, self.guard)
        self.clock = [10.0]
        self.observer = GatewayDrainTargetObserver(control=self.control, clock=lambda: self.clock[0], **settings)

    def working(self, key, strategy="s1"):
        command(self.journal, strategy=strategy, key=key, anchor=key + "-anchor", status="SENT")
        self.returned(key, DirectOrderStatus.ACCEPTED)

    def returned(self, key, status, *, filled="0", ref=None):
        result = self.ledger.ingest(replace(
            fact(kind=DirectExecutionEventKind.ORDER, status=status, keys=(key + "-anchor", "venue-" + key),
                 binding=(key + "-anchor",), venue="venue-" + key, ref=ref or f"{key}-{status.value}-{filled}"),
            order_quantity="2", filled_quantity=filled))
        assert result.disposition == "ATTRIBUTED", result

    def drain(self, cause, **scope):
        self.control.apply(cause, "DRAIN", origin="operator", **(scope or {"scope_kind": "account"}))

    def states(self, cause):
        return {row["request_key"]: row["state"] for row in self.control.targets(cause)}

    def revision(self):
        with self.journal._transaction() as db:
            return db.execute("SELECT revision FROM direct_journal_meta WHERE account='acct'").fetchone()[0]

    def step(self, seconds=1.0):
        self.clock[0] += seconds
        self.observer.maintain()


@pytest.fixture
def world(tmp_path):
    item = World(tmp_path)
    yield item
    item.guard.release()


def test_a_target_that_ended_is_shown_as_ended_without_an_operator_reconcile(world, caplog):
    caplog.set_level(logging.INFO, logger="bt_api_py.gateway_drain_observer")
    world.working("one")
    world.working("two")
    world.drain("ops-drain", scope_kind="strategy", scope_value="s1")
    world.step()
    assert world.states("ops-drain") == {"one": "PENDING", "two": "PENDING"}        # nothing ended yet
    assert caplog.records == []
    world.returned("one", DirectOrderStatus.CANCELLED)
    assert world.states("ops-drain")["one"] == "PENDING"                            # stored state is not current yet
    world.step()
    assert world.states("ops-drain") == {"one": "OBSERVED_TERMINAL", "two": "PENDING"}
    assert [record.getMessage() for record in caplog.records] == [
        "DRAIN cause ops-drain (scope strategy s1): target states updated from order returns"]
    # A replay of the old "accepted" return is the same fact and changes nothing ...
    world.returned("one", DirectOrderStatus.ACCEPTED, ref="replayed")
    world.step()
    assert world.states("ops-drain")["one"] == "OBSERVED_TERMINAL" and len(caplog.records) == 1
    # ... a later, different return that contradicts the end takes it back, as an operator's reconcile would.
    world.returned("one", DirectOrderStatus.PARTIALLY_FILLED, filled="1")
    world.step()
    assert world.states("ops-drain")["one"] == "UNKNOWN"
    # Unchanged states write nothing and log nothing more.
    revision = world.revision()
    world.step()
    assert world.revision() == revision and len(caplog.records) == 2


def test_it_only_observes(world):
    world.working("one")
    world.drain("ops-drain")
    world.control.apply("stop", "STOP_NEW", scope_kind="account", origin="operator")
    world.returned("one", DirectOrderStatus.FILLED, filled="2")
    world.step()
    # The cause stays active at its version, no cancel was claimed, other causes are untouched.
    cause, = world.control.active_drain_causes()
    assert (cause["cause_id"], cause["active"], cause["version"]) == ("ops-drain", 1, 1)
    with world.journal._transaction() as db:
        # No cancel was claimed for the target: its state is an observation, not a cancel state.
        assert db.execute("SELECT count(*) FROM direct_control_targets WHERE state NOT IN "
                          "('PENDING','OBSERVED_TERMINAL','UNKNOWN')").fetchone()[0] == 0
        assert db.execute("SELECT active FROM direct_control_causes WHERE cause_id='stop'").fetchone()[0] == 1
    assert world.states("ops-drain") == {"one": "OBSERVED_TERMINAL"}


def test_a_released_cause_is_left_alone(world):
    world.working("one")
    world.drain("ops-drain")
    world.control.resume("ops-drain", expected_version=1, operation_id="resume-1")
    world.returned("one", DirectOrderStatus.CANCELLED)
    before, revision = world.states("ops-drain"), world.revision()
    calls = []
    original = type(world.control).reconcile_targets
    type(world.control).reconcile_targets = lambda self, cause_id, **kwargs: calls.append(cause_id)
    try:
        world.step()
    finally:
        type(world.control).reconcile_targets = original
    assert calls == [] and world.states("ops-drain") == before and world.revision() == revision


def test_at_most_one_pass_per_interval_and_only_on_the_owner_thread(world):
    world.working("one")
    world.drain("ops-drain")
    world.step()
    world.returned("one", DirectOrderStatus.CANCELLED)
    world.step(0.5)                                              # too early
    assert world.states("ops-drain") == {"one": "PENDING"}
    world.clock[0] += 5
    other = threading.Thread(target=world.observer.maintain)     # another thread does nothing
    other.start()
    other.join()
    assert world.states("ops-drain") == {"one": "PENDING"}
    world.observer.maintain()
    assert world.states("ops-drain") == {"one": "OBSERVED_TERMINAL"}


def test_many_causes_are_visited_in_turn(tmp_path):
    world = World(tmp_path, causes_per_pass=2)
    try:
        names = [f"drain-{index}" for index in range(5)]
        for index, name in enumerate(names):
            world.working(f"order-{index}", strategy=f"s{index}")
            world.drain(name, scope_kind="strategy", scope_value=f"s{index}")
        for index in range(5):
            world.returned(f"order-{index}", DirectOrderStatus.CANCELLED)

        def ended():
            return [name for name in names if "OBSERVED_TERMINAL" in world.states(name).values()]

        world.step()
        assert ended() == names[:2]
        world.step()
        assert ended() == names[:4]
        world.step()
        assert ended() == names                                  # the short last page ends the round
        world.returned("order-0", DirectOrderStatus.PARTIALLY_FILLED, filled="1")
        world.step()                                             # the next round starts from the first cause
        assert world.states("drain-0") == {"order-0": "UNKNOWN"}
    finally:
        world.guard.release()


def test_a_full_last_page_still_starts_over(tmp_path):
    world = World(tmp_path, causes_per_pass=2)
    try:
        for index in range(2):
            world.working(f"order-{index}", strategy=f"s{index}")
            world.drain(f"drain-{index}", scope_kind="strategy", scope_value=f"s{index}")
        world.step()                                             # both visited; the page was full
        world.returned("order-0", DirectOrderStatus.CANCELLED)
        world.step()                                             # nothing after the cursor: starts over at once
        assert world.states("drain-0") == {"order-0": "OBSERVED_TERMINAL"}
    finally:
        world.guard.release()


def test_a_short_list_costs_one_listing_per_pass(world, monkeypatch):
    world.working("one")
    world.drain("ops-drain")
    listings = []
    original = type(world.control).active_drain_causes

    def listed(self, **kwargs):
        listings.append(kwargs["after"])
        return original(self, **kwargs)

    monkeypatch.setattr(type(world.control), "active_drain_causes", listed)
    for _ in range(3):
        world.step()
    assert listings == ["", "", ""]                              # the round ended with the short page


def test_a_failure_is_logged_once_and_the_next_pass_works_again(world, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="bt_api_py.gateway_drain_observer")
    world.working("one")
    world.drain("ops-drain")
    world.returned("one", DirectOrderStatus.CANCELLED)
    original = type(world.control).reconcile_targets
    broken = [True]

    def reconcile(self, cause_id, **kwargs):
        if broken[0]:
            raise DirectCommandJournalError("JOURNAL_FENCE_LOST")
        return original(self, cause_id, **kwargs)

    monkeypatch.setattr(type(world.control), "reconcile_targets", reconcile)
    world.step()
    world.step()                                                 # must not raise; the same failure is not repeated
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1 and errors[0].getMessage().endswith(": JOURNAL_FENCE_LOST")
    assert world.states("ops-drain") == {"one": "PENDING"}
    broken[0] = False
    world.step()
    assert world.states("ops-drain") == {"one": "OBSERVED_TERMINAL"}
    assert "DRAIN targets are being reconciled again" in [record.getMessage() for record in caplog.records]


@pytest.mark.parametrize("bad", [
    {"control": object()}, {"interval_seconds": 0}, {"interval_seconds": 61}, {"interval_seconds": True},
    {"causes_per_pass": 0}, {"causes_per_pass": 257}, {"causes_per_pass": 1.0}, {"clock": None}])
def test_invalid_settings_are_refused(world, bad):
    with pytest.raises(ValueError, match="^INVALID_GATEWAY_DRAIN_OBSERVER$"):
        GatewayDrainTargetObserver(**{"control": world.control, **bad})
