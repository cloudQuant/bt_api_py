"""In-memory observation invalidation tests; no host, network, ledger or account."""

import pytest

from bt_api_py.risk_service_gateway import RiskFactsCache
from tests.unit.test_iter64_observation_event_day_fake import EPOCH, FakeEngine, FakeObserver, event, make_pump


def test_restart_forgets_old_event_day_but_keeps_market_day_and_empty_replay():
    facts = RiskFactsCache({})
    facts.note_event_day("20261120")
    facts.note_tick("x", 1, None, "20261114")
    engine, observer = FakeEngine(), FakeObserver([])
    pump = make_pump(engine, observer, facts)
    assert pump.poll() == 0
    assert facts.trading_day() == "20261114"
    assert pump.verified_epoch == EPOCH and observer.acks == []
    assert engine.gaps == ["observation_restart"]


def test_cache_null_and_max_semantics_are_unchanged():
    facts = RiskFactsCache({})
    facts.note_event_day("20261115")
    facts.note_event_day(None)
    facts.note_event_day("20261114")
    assert facts.trading_day() == "20261115"
    facts.forget_event_day()
    assert facts.trading_day() is None


@pytest.mark.parametrize("failure", ["replace", "read", "ack", "epoch"])
def test_failures_clear_observation_day(failure):
    class Observer(FakeObserver):
        fail = False

        def read_after(self, **kwargs):
            if self.fail and failure == "read":
                raise RuntimeError("synthetic")
            result = super().read_after(**kwargs)
            if self.fail and failure == "epoch":
                result.page.progress.epoch = "f" * 64
            return result

        def acknowledge(self, *args, **kwargs):
            if failure == "ack":
                raise RuntimeError("synthetic")
            return super().acknowledge(*args, **kwargs)

    observer, engine, facts = Observer([event(1, "20261115")]), FakeEngine(), RiskFactsCache({})
    pump = make_pump(engine, observer, facts)
    assert pump.poll() == 1
    if failure != "ack":
        assert facts.trading_day() == "20261115"
        if failure == "replace":
            pump.replace_observer(FakeObserver([]))
        else:
            observer.fail = True
            pump.poll()
    assert facts.trading_day() is None
    assert pump.verified_epoch is None
    if failure == "ack":
        assert observer.acks == [] and pump._after == 0


@pytest.mark.parametrize("day", [None, "invalid", "20260229", "２０２６１１１４", "20261131"])
def test_invalid_day_only_acks_valid_prefix(day):
    engine = FakeEngine()
    observer = FakeObserver([event(1, "20261114"), event(2, "20261115"),
                             event(3, day), event(4, "20261116")])
    facts = RiskFactsCache({})
    pump = make_pump(engine, observer, facts)
    assert pump.poll() == 2
    assert [item.sequence for item in engine.applied] == [0, 1]
    assert observer.acks == [2] and pump._after == 2
    assert facts.trading_day() is None and pump.verified_epoch is None
    assert pump.last_code == "observation_unmappable"


def test_missing_first_day_is_not_applied_or_acknowledged():
    engine, observer, facts = FakeEngine(), FakeObserver([event(1, None)]), RiskFactsCache({})
    pump = make_pump(engine, observer, facts)
    assert pump.poll() == 0
    assert engine.applied == [] and observer.acks == [] and pump._after == 0
    assert facts.trading_day() is None and pump.verified_epoch is None


@pytest.mark.parametrize("broken", ["missing_day", "unmappable"])
def test_broken_suffix_revokes_day_before_apply_and_prefix_ack(broken):
    observed = []

    class Engine(FakeEngine):
        def apply_observations_bound(self, events):
            observed.append(("apply", facts._event_day, facts.trading_day()))
            facts.note_event_day("20261122")  # callback writes before ACK
            return super().apply_observations_bound(events)

    class Observer(FakeObserver):
        def acknowledge(self, sequence, **kwargs):
            observed.append(("ack", facts._event_day, facts.trading_day()))
            return super().acknowledge(sequence, **kwargs)

    engine, observer, facts = Engine(), Observer([]), RiskFactsCache({})
    facts.note_tick("x", 1, None, "20261114")
    pump = make_pump(engine, observer, facts)
    assert pump.poll() == 0
    facts.note_event_day("20261120")
    suffix = event(2, None) if broken == "missing_day" else object()
    observer.events = [event(1, "20261115"), suffix, event(3, "20261116")]
    assert pump.poll() == 1
    assert observed == [("apply", None, "20261114"), ("ack", None, "20261114")]
    assert [item.sequence for item in engine.applied] == [0] and observer.acks == [1]
    assert pump._after == 1 and facts._event_day is None and pump.verified_epoch is None
    assert pump.last_code == "observation_unmappable"


@pytest.mark.parametrize("failure", ["throw", "poll", "replace"])
def test_broken_suffix_clear_failure_or_reentry_never_applies_or_acks(failure):
    class Facts(RiskFactsCache):
        fail = False

        def forget_event_day(self):
            if self.fail:
                if failure == "throw":
                    raise RuntimeError("synthetic")
                if failure == "poll":
                    self.pump.poll()
                else:
                    self.pump.replace_observer(FakeObserver([]))
            super().forget_event_day()

    engine, observer, facts = FakeEngine(), FakeObserver([]), Facts({})
    pump = make_pump(engine, observer, facts)
    facts.pump = pump
    assert pump.poll() == 0
    facts.note_event_day("20261120")
    facts.fail = True
    observer.events = [event(1, "20261115"), event(2, None)]
    assert pump.poll() == 0
    assert engine.applied == [] and observer.acks == [] and pump._after == 0
    assert pump._needs_reset and pump.verified_epoch is None
    assert pump._event_day_clear_required or pump._gap_record_failed


def test_broken_suffix_post_apply_clear_failure_never_acks():
    class Facts(RiskFactsCache):
        fail = False

        def forget_event_day(self):
            if self.fail:
                raise RuntimeError("synthetic")
            super().forget_event_day()

    class Engine(FakeEngine):
        def apply_observations_bound(self, events):
            result = super().apply_observations_bound(events)
            facts.fail = True
            return result

    engine, observer, facts = Engine(), FakeObserver([]), Facts({})
    pump = make_pump(engine, observer, facts)
    assert pump.poll() == 0
    observer.events = [event(1, "20261115"), event(2, None)]
    assert pump.poll() == 1
    assert [item.sequence for item in engine.applied] == [0]
    assert observer.acks == [] and pump._after == 0 and pump.verified_epoch is None
    assert pump._needs_reset and pump._event_day_clear_required


def test_projection_reentry_then_write_is_cleared_after_callback_returns():
    class Facts(RiskFactsCache):
        def note_event_day(self, day):
            self.pump.replace_observer(FakeObserver([]))
            super().note_event_day(day)

    engine, observer, facts = FakeEngine(), FakeObserver([event(1, "20261115")]), Facts({})
    pump = make_pump(engine, observer, facts)
    facts.pump = pump
    assert pump.poll() == 1
    assert facts.trading_day() is None and observer.acks == [] and pump.verified_epoch is None


def test_projection_that_writes_then_raises_is_cleared():
    class Facts(RiskFactsCache):
        def note_event_day(self, day):
            super().note_event_day(day)
            raise RuntimeError("synthetic")

    engine, observer, facts = FakeEngine(), FakeObserver([event(1, "20261115")]), Facts({})
    pump = make_pump(engine, observer, facts)
    assert pump.poll() == 1
    assert facts.trading_day() is None and observer.acks == []
    assert pump.last_code == "observation_fact_projection_failed" and pump.verified_epoch is None


def test_mark_sees_revoked_event_day_and_reinjected_day_is_revoked():
    class Engine(FakeEngine):
        def mark_observation_gap(self, reason):
            super().mark_observation_gap(reason)
            assert facts._event_day is None
            assert facts.trading_day() == "20261114"
            facts.note_event_day("20261125")

    facts, engine = RiskFactsCache({}), Engine()
    facts.note_tick("x", 1, None, "20261114")
    facts.note_event_day("20261120")
    pump = make_pump(engine, FakeObserver([]), facts)
    assert pump.poll() == 0
    assert facts._event_day is None and facts.trading_day() == "20261114"
    assert pump.verified_epoch == EPOCH


@pytest.mark.parametrize("failure", ["throw", "reenter"])
def test_invalidation_failure_prevents_mark_then_retries(failure):
    class Facts(RiskFactsCache):
        first = True

        def forget_event_day(self):
            if self.first:
                self.first = False
                if failure == "throw":
                    raise RuntimeError("synthetic")
                self.pump.replace_observer(FakeObserver([]))
            super().forget_event_day()

    facts, engine, observer = Facts({}), FakeEngine(), FakeObserver([])
    facts.note_event_day("20261120")
    pump = make_pump(engine, observer, facts)
    facts.pump = pump
    assert pump.poll() == 0
    assert engine.gaps == [] and engine.epoch is None and pump.verified_epoch is None
    assert pump._gap_record_failed and observer.acks == []
    assert pump.poll() == 0
    assert engine.gaps == ["observation_restart"] and pump.verified_epoch == EPOCH
    assert facts.trading_day() is None


@pytest.mark.parametrize("failure", ["throw", "reenter"])
def test_mark_failure_or_reentry_revokes_callback_write_then_retries(failure):
    class Engine(FakeEngine):
        first = True

        def mark_observation_gap(self, reason):
            super().mark_observation_gap(reason)
            assert facts._event_day is None
            if self.first:
                self.first = False
                if failure == "reenter":
                    self.pump.replace_observer(FakeObserver([]))
                facts.note_event_day("20261125")
                if failure == "throw":
                    raise RuntimeError("synthetic")

    facts, engine, observer = RiskFactsCache({}), Engine(), FakeObserver([])
    facts.note_event_day("20261120")
    pump = make_pump(engine, observer, facts)
    engine.pump = pump
    assert pump.poll() == 0
    assert engine.gaps == ["observation_restart"] and engine.epoch is None
    assert facts._event_day is None and pump.verified_epoch is None and observer.acks == []
    assert pump.poll() == 0
    assert engine.gaps == ["observation_restart", "observation_restart"]
    assert facts._event_day is None and pump.verified_epoch == EPOCH


def test_successful_mark_is_not_repeated_while_post_clear_fails_then_recovers():
    class Facts(RiskFactsCache):
        calls = 0

        def forget_event_day(self):
            self.calls += 1
            if 2 <= self.calls <= 4:
                raise RuntimeError("synthetic")
            super().forget_event_day()

    engine, observer, facts = FakeEngine(), FakeObserver([]), Facts({})
    pump = make_pump(engine, observer, facts)
    for expected_calls in (2, 3, 4):
        assert pump.poll() == 0
        assert facts.calls == expected_calls and engine.gaps == ["observation_restart"]
        assert not pump._gap_record_failed and pump._event_day_clear_required
        assert engine.epoch is None and observer.acks == [] and pump.verified_epoch is None
    assert pump.poll() == 0
    assert facts.calls == 5 and engine.gaps == ["observation_restart"]
    assert not pump._event_day_clear_required and pump.verified_epoch == EPOCH


def test_post_clear_reentry_changes_generation_and_requires_new_mark():
    class Facts(RiskFactsCache):
        calls = 0

        def forget_event_day(self):
            self.calls += 1
            if self.calls == 2:
                self.pump.replace_observer(FakeObserver([]))
            super().forget_event_day()

    engine, observer, facts = FakeEngine(), FakeObserver([]), Facts({})
    pump = make_pump(engine, observer, facts)
    facts.pump = pump
    assert pump.poll() == 0
    assert facts.calls == 2 and engine.gaps == ["observation_restart"]
    assert pump._gap_record_failed and pump._event_day_clear_required
    assert engine.epoch is None and observer.acks == [] and pump.verified_epoch is None
    assert pump.poll() == 0
    assert engine.gaps == ["observation_restart", "observation_restart"]
    assert not pump._gap_record_failed and not pump._event_day_clear_required
    assert pump.verified_epoch == EPOCH


def test_later_clear_retry_reentry_requires_new_mark():
    class Facts(RiskFactsCache):
        calls = 0

        def forget_event_day(self):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("synthetic")
            if self.calls == 3:
                self.pump.replace_observer(FakeObserver([]))
            super().forget_event_day()

    engine, observer, facts = FakeEngine(), FakeObserver([]), Facts({})
    pump = make_pump(engine, observer, facts)
    facts.pump = pump
    assert pump.poll() == 0
    assert engine.gaps == ["observation_restart"] and not pump._gap_record_failed
    assert pump._event_day_clear_required and pump.verified_epoch is None
    assert pump.poll() == 0
    assert engine.gaps == ["observation_restart"] and pump._gap_record_failed
    assert pump._event_day_clear_required and engine.epoch is None and observer.acks == []
    assert pump.poll() == 0
    assert engine.gaps == ["observation_restart", "observation_reentrant"]
    assert pump.verified_epoch == EPOCH


@pytest.mark.parametrize("reentry", ["poll", "poll_then_raise", "replace_then_raise"])
def test_clear_retry_reentry_including_throw_requires_new_mark(reentry):
    class Facts(RiskFactsCache):
        calls = 0

        def forget_event_day(self):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("synthetic post-clear failure")
            if self.calls == 3:
                if reentry == "replace_then_raise":
                    self.pump.replace_observer(FakeObserver([]))
                else:
                    assert self.pump.poll() == 0
                if reentry.endswith("raise"):
                    raise RuntimeError("synthetic reentrant failure")
            super().forget_event_day()

    engine, observer, facts = FakeEngine(), FakeObserver([]), Facts({})
    pump = make_pump(engine, observer, facts)
    facts.pump = pump
    assert pump.poll() == 0
    assert engine.gaps == ["observation_restart"] and not pump._gap_record_failed
    assert pump._event_day_clear_required and pump.verified_epoch is None
    assert pump.poll() == 0
    assert engine.gaps == ["observation_restart"] and pump._gap_record_failed
    assert pump._event_day_clear_required and engine.epoch is None and observer.acks == []
    assert pump.verified_epoch is None
    assert pump.poll() == 0
    assert engine.gaps == ["observation_restart", "observation_reentrant"]
    assert not pump._gap_record_failed and not pump._event_day_clear_required
    assert pump.verified_epoch == EPOCH and observer.acks == []


def test_repeated_progress_failure_does_not_repeat_persisted_gap():
    class Observer(FakeObserver):
        progress_calls = 0

        def progress(self, *, timeout_seconds):
            self.progress_calls += 1
            raise RuntimeError("synthetic")

    engine, observer, facts = FakeEngine(), Observer([]), RiskFactsCache({})
    pump = make_pump(engine, observer, facts)
    for expected in range(1, 5):
        assert pump.poll() == 0
        assert observer.progress_calls == expected and engine.gaps == ["observation_restart"]
        assert not pump._gap_record_failed and pump.verified_epoch is None
        assert engine.epoch is None and engine.applied == [] and observer.acks == []


def test_repeated_mark_failure_is_retried_without_reset_until_success():
    class Engine(FakeEngine):
        def mark_observation_gap(self, reason):
            super().mark_observation_gap(reason)
            if len(self.gaps) <= 2:
                raise RuntimeError("synthetic")

    engine, observer, facts = Engine(), FakeObserver([]), RiskFactsCache({})
    pump = make_pump(engine, observer, facts)
    for expected_calls in (1, 2):
        assert pump.poll() == 0
        assert len(engine.gaps) == expected_calls and pump._gap_record_failed
        assert engine.epoch is None and observer.acks == [] and pump.verified_epoch is None
    assert pump.poll() == 0
    assert len(engine.gaps) == 3 and not pump._gap_record_failed
    assert pump.verified_epoch == EPOCH


@pytest.mark.parametrize("missing_hook", [False, True])
def test_failed_or_missing_invalidation_prevents_reset_and_ack(missing_hook):
    class Facts:
        def note_event_day(self, day):
            raise AssertionError("must not project")

    if not missing_hook:
        def fail(self):
            raise RuntimeError("synthetic")
        Facts.forget_event_day = fail
    engine, observer = FakeEngine(), FakeObserver([event(1, "20261115")])
    pump = make_pump(engine, observer, Facts())
    assert pump.poll() == 0
    assert pump.last_code == "observation_fact_invalidation_failed"
    assert engine.epoch is None and engine.applied == [] and observer.acks == []
    assert pump.verified_epoch is None


def test_gap_mark_callback_reentry_does_not_recursively_mark():
    class Engine(FakeEngine):
        def mark_observation_gap(self, reason):
            super().mark_observation_gap(reason)
            self.pump.replace_observer(FakeObserver([]))

    engine, facts = Engine(), RiskFactsCache({})
    facts.note_event_day("20261120")
    pump = make_pump(engine, FakeObserver([]), facts)
    engine.pump = pump
    pump.poll()
    assert engine.gaps == ["observation_restart"]
    assert facts.trading_day() is None
    assert pump.verified_epoch is None and engine.epoch is None


def test_gap_mark_failure_does_not_claim_replay():
    class Engine(FakeEngine):
        def mark_observation_gap(self, reason):
            raise RuntimeError("synthetic")

    engine, observer, facts = Engine(), FakeObserver([]), RiskFactsCache({})
    facts.note_event_day("20261120")
    pump = make_pump(engine, observer, facts)
    assert pump.poll() == 0
    assert facts.trading_day() is None and engine.epoch is None
    assert pump.verified_epoch is None and observer.acks == []


def test_reentrant_invalidation_does_not_claim_cleanup():
    class Facts(RiskFactsCache):
        def forget_event_day(self):
            self.pump.replace_observer(FakeObserver([]))
            super().forget_event_day()

    engine, observer, facts = FakeEngine(), FakeObserver([event(1, "20261115")]), Facts({})
    pump = make_pump(engine, observer, facts)
    facts.pump = pump
    assert pump.poll() == 0
    assert pump._event_day_clear_required and pump.verified_epoch is None
    assert engine.epoch is None and engine.applied == [] and observer.acks == []


@pytest.mark.parametrize("change", ["replace", "poll", "recursive", "epoch", "after", "revision",
                                     "target", "generation", "clear_required", "gap_pending", "engine"])
def test_verified_epoch_rechecks_snapshot_after_cursor_callback(change):
    class Engine(FakeEngine):
        callback = None

        def observation_cursor_matches(self, epoch, sequence, revision):
            matches = super().observation_cursor_matches(epoch, sequence, revision)
            callback, self.callback = self.callback, None
            if callback is not None:
                callback()
            return matches

    engine, observer, facts = Engine(), FakeObserver([]), RiskFactsCache({})
    pump = make_pump(engine, observer, facts)
    assert pump.poll() == 0 and pump.verified_epoch == EPOCH

    def mutate():
        if change == "replace":
            pump.replace_observer(FakeObserver([]))
        elif change == "poll":
            pump.poll()
        elif change == "recursive":
            assert pump.verified_epoch is None
        elif change == "epoch":
            pump._epoch = "f" * 64
        elif change == "after":
            pump._after += 1
        elif change == "revision":
            pump._cursor_revision = "changed"
        elif change == "target":
            pump._replay_target += 1
        elif change == "generation":
            pump._generation += 1
        elif change == "clear_required":
            pump._event_day_clear_required = True
        elif change == "engine":
            pump._engine = FakeEngine()
        else:
            pump._gap_record_failed = True

    engine.callback = mutate
    assert pump.verified_epoch is None


def test_verified_epoch_handles_cursor_lookup_reentry():
    class Engine(FakeEngine):
        pump = None
        intercept = False

        def __getattribute__(self, name):
            if name == "observation_cursor_matches" and object.__getattribute__(self, "intercept"):
                object.__setattr__(self, "intercept", False)
                assert object.__getattribute__(self, "pump").verified_epoch is None
            return super().__getattribute__(name)

    engine, observer, facts = Engine(), FakeObserver([]), RiskFactsCache({})
    pump = make_pump(engine, observer, facts)
    engine.pump = pump
    assert pump.poll() == 0
    engine.intercept = True
    assert pump.verified_epoch is None
    assert pump.verified_epoch == EPOCH


def test_verified_epoch_cursor_callback_poll_does_not_read_apply_or_ack():
    class Observer(FakeObserver):
        reads = 0

        def read_after(self, **kwargs):
            self.reads += 1
            return super().read_after(**kwargs)

    class Engine(FakeEngine):
        callback = None

        def observation_cursor_matches(self, epoch, sequence, revision):
            matches = super().observation_cursor_matches(epoch, sequence, revision)
            callback, self.callback = self.callback, None
            if callback is not None:
                assert callback() == 0
            return matches

    engine, observer, facts = Engine(), Observer([]), RiskFactsCache({})
    pump = make_pump(engine, observer, facts)
    assert pump.poll() == 0
    reads, applied, acks = observer.reads, list(engine.applied), list(observer.acks)
    engine.callback = pump.poll
    assert pump.verified_epoch is None
    assert observer.reads == reads and engine.applied == applied and observer.acks == acks
    assert pump.verified_epoch == EPOCH
