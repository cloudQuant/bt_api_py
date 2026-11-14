"""ObservationPump with fake ports; no ledger, host, network, or account proof."""

from types import SimpleNamespace

import pytest

from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_py.risk_service_gateway import ObservationPump, RiskFactsCache
from bt_api_risk.pretrade.engine import ObservationApplyResult


EPOCH = "e" * 64
KEY = MarketInstrumentKey("synthetic-provider", "SHFE", "synthetic-env", "FUTURE", "x", "x")


def event(sequence, day):
    observation = SimpleNamespace(
        kind=DirectExecutionEventKind.ORDER, side="buy", offset="open", order_quantity="1",
        filled_quantity="0", limit_price="10", status=DirectOrderStatus.ACCEPTED,
        trade_quantity=None, trade_price=None, trade_key_digest=None,
        trading_day=day, venue_key_digest="synthetic-order", instrument=KEY,
    )
    return SimpleNamespace(strategy_scope=None, event=SimpleNamespace(
        sequence=sequence, request_key="synthetic-order", reservation_id=None,
        command_principal="external", observation=observation))


class FakeEngine:
    def __init__(self, *, blocked=False, reject_applied=False):
        self.blocked, self.reject_applied = blocked, reject_applied
        self.epoch, self.last, self.revision = None, -1, 0
        self.gaps, self.cursor_calls, self.applied = [], 0, []

    def mark_observation_gap(self, reason):
        self.gaps.append(reason)

    def reset_observations_bound(self, epoch, head):
        self.epoch, self.last = epoch, -1
        self.revision += 1
        return ObservationApplyResult(epoch, -1, 0, 0, False, str(self.revision))

    def apply_observations_bound(self, events):
        self.applied.extend(events)
        self.last = events[-1].sequence
        self.revision += 1
        return ObservationApplyResult(self.epoch, self.last, len(events), len(events),
                                      self.blocked, str(self.revision))

    def observation_cursor_matches(self, epoch, sequence, revision):
        self.cursor_calls += 1
        return (not (self.reject_applied and sequence >= 0)
                and (epoch, sequence, revision) == (self.epoch, self.last, str(self.revision)))


class FakeObserver:
    def __init__(self, events):
        self.events = events
        self.acks = []

    def progress(self, *, timeout_seconds):
        return SimpleNamespace(outcome="OBSERVED", progress=SimpleNamespace(
            epoch=EPOCH, offered_sequence=len(self.events), acknowledged_sequence=0))

    def read_after(self, *, after_sequence, expected_epoch, limit, timeout_seconds):
        return SimpleNamespace(outcome="OBSERVED", page=SimpleNamespace(
            progress=SimpleNamespace(epoch=EPOCH, offered_sequence=len(self.events),
                                     acknowledged_sequence=0),
            events=self.events, has_more=False))

    def acknowledge(self, sequence, *, expected_epoch, timeout_seconds):
        self.acks.append(sequence)
        return SimpleNamespace(outcome="OBSERVED", progress=SimpleNamespace(
            epoch=expected_epoch, offered_sequence=len(self.events), acknowledged_sequence=sequence))


def make_pump(engine, observer, facts):
    return ObservationPump(engine, observer, facts, risk_principal="synthetic-risk",
                           intent_payload=lambda key: None, timeout_seconds=1)


def test_blocked_full_page_does_not_project_day_or_ack():
    engine = FakeEngine(blocked=True)
    observer = FakeObserver([event(1, "20261115")])
    facts = RiskFactsCache({})
    value = make_pump(engine, observer, facts)
    assert value.poll() == 1
    assert facts.trading_day() is None and observer.acks == [] and value._after == 0
    assert value.verified_epoch is None and engine.gaps[-1] == "observation_partial_apply"


def test_cursor_mismatch_does_not_project_day_or_ack():
    engine = FakeEngine(reject_applied=True)
    observer = FakeObserver([event(1, "20261115")])
    facts = RiskFactsCache({})
    value = make_pump(engine, observer, facts)
    assert value.poll() == 1
    assert facts.trading_day() is None and observer.acks == [] and value._after == 0
    assert value.verified_epoch is None and value.last_code == "observation_apply_unproven"
    assert engine.gaps[-1] == "observation_apply_unproven"


def test_healthy_page_projects_max_day_once():
    class CountingFacts(RiskFactsCache):
        def __init__(self):
            super().__init__({})
            self.calls = []

        def note_event_day(self, day):
            self.calls.append(day)
            super().note_event_day(day)

    facts = CountingFacts()
    days = ["20261114", "20261113", "20261116", "20261115"]
    engine = FakeEngine()
    observer = FakeObserver([event(i + 1, day) for i, day in enumerate(days)])
    value = make_pump(engine, observer, facts)
    assert value.poll() == len(days)
    assert facts.calls == ["20261116"] and facts.trading_day() == "20261116"
    assert observer.acks == [len(days)] and value._after == len(days)
    assert engine.cursor_calls == 4  # reset, pre-projection, pre-ACK, post-ACK
    assert value.verified_epoch == EPOCH


@pytest.mark.parametrize("failure", ["replace", "reenter", "cursor"])
def test_projection_callback_invalidation_stops_ack(failure):
    class CallbackFacts(RiskFactsCache):
        callback = None

        def note_event_day(self, day):
            super().note_event_day(day)
            self.callback()

    engine = FakeEngine()
    observer = FakeObserver([event(1, "20261115"), event(2, "20261116")])
    replacement = FakeObserver([])
    facts = CallbackFacts({})
    value = make_pump(engine, observer, facts)
    cursor_calls_at_callback = []

    def invalidate():
        if failure == "replace":
            value.replace_observer(replacement)
        elif failure == "reenter":
            value.poll()
        else:
            engine.reject_applied = True
        cursor_calls_at_callback.append(engine.cursor_calls)

    facts.callback = invalidate
    assert value.poll() == 2
    assert observer.acks == [] and replacement.acks == [] and value._after == 0
    # Local invalidation is checked before a cursor query; a cursor-only change
    # requires exactly one query. This count excludes verified_epoch below.
    assert engine.cursor_calls == cursor_calls_at_callback[0] + (failure == "cursor")
    assert value.verified_epoch is None and value.last_code == "observation_apply_unproven"
    assert engine.gaps[-1] == ("observation_reconnect" if failure == "replace"
                               else "observation_apply_unproven")
    assert facts.trading_day() is None


def test_mapping_failure_after_valid_prefix_preserves_prefix_ack_semantics():
    engine = FakeEngine()
    observer = FakeObserver([event(1, "20261114"), object()])
    facts = RiskFactsCache({})
    value = make_pump(engine, observer, facts)
    assert value.poll() == 1
    assert [item.sequence for item in engine.applied] == [0]
    assert facts.trading_day() is None and observer.acks == [1] and value._after == 1
    assert engine.gaps[-1] == "observation_unmappable" and value.verified_epoch is None
