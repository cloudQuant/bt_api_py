"""ObservationPump partial application, epoch and ACK tests using local fakes."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_risk.pretrade.engine import ObservationApplyResult

from tests.unit.test_iter64_risk_service_component import service as _service_fixture
from tests.unit.test_iter64_risk_service_gateway import (
    EPOCH,
    Engine,
    Observer,
    account_event,
    page,
    pump,
)


@pytest.fixture
def service(tmp_path):
    yield from _service_fixture.__wrapped__(tmp_path)


class PrefixEngine(Engine):
    def __init__(self, limit):
        super().__init__()
        self.limit = limit

    def apply_observations_bound(self, events):
        prefix = events[:self.limit]
        previous = self.last
        self.applied.extend(prefix)
        self.last = max([self.last] + [event.sequence for event in prefix])
        self.revision += 1
        return ObservationApplyResult(self.epoch, self.last,
                                      sum(event.sequence > previous for event in prefix),
                                      len(prefix), len(prefix) != len(events), str(self.revision))


@pytest.mark.parametrize("limit", [0, 1])
def test_partial_or_zero_apply_stops_without_ack(limit):
    engine = PrefixEngine(limit)
    observer = Observer([page([account_event(1), account_event(2), account_event(3)], has_more=True)],
                        offered=3)
    value = pump(engine, observer)
    assert value.poll() == limit
    assert len(observer.reads) == 1 and observer.acks == []
    assert value.verified_epoch is None and engine.gaps[-1] == "observation_partial_apply"


def test_sequence_gap_applies_and_acks_only_contiguous_prefix():
    engine = Engine()
    observer = Observer([page([account_event(1), account_event(3)], has_more=False)], offered=3)
    value = pump(engine, observer)
    assert value.poll() == 1
    assert [event.sequence for event in engine.applied] == [0]
    assert observer.acks == [1] and value.verified_epoch is None
    assert engine.gaps[-1] == "observation_sequence_gap"


def test_duplicate_page_does_not_double_count_events():
    engine = Engine()
    observer = Observer([page([account_event(1)]), page([account_event(1), account_event(2)])])
    value = pump(engine, observer)
    assert value.poll() == 1
    assert value.poll() == 1
    assert observer.acks == [1, 2]


def test_same_epoch_replay_does_not_count_same_event_twice():
    engine = Engine()
    original = Observer([page([account_event(1)])], offered=1)
    value = pump(engine, original)
    assert value.poll() == 1
    replacement = Observer([page([account_event(1)])], offered=1, acknowledged=1)
    value.replace_observer(replacement)
    assert value.poll() == 0
    assert replacement.acks == [1] and value.verified_epoch == EPOCH


def test_same_epoch_ack_regression_cannot_start_replay():
    engine = Engine()
    original = Observer([page([account_event(1)])], offered=1)
    value = pump(engine, original)
    assert value.poll() == 1 and original.acks == [1]
    replacement = Observer([], offered=1, acknowledged=0)
    value.replace_observer(replacement)
    assert value.poll() == 0 and replacement.reads == [] and value.verified_epoch is None
    assert value.last_code == "observation_ack_regressed"


def test_no_progress_with_has_more_exits_and_marks_gap():
    engine = Engine()
    observer = Observer([page([], has_more=True)], offered=0)
    value = pump(engine, observer)
    assert value.poll() == 0
    assert len(observer.reads) == 1 and observer.acks == [] and value.verified_epoch is None


@pytest.mark.parametrize("value", [5, -1, None, True])
def test_empty_page_with_uncovered_or_invalid_progress_is_unverified(value):
    engine = Engine()
    response = page([])
    response.page.progress.offered_sequence = value
    observer = Observer([response], offered=0)
    result = pump(engine, observer)
    assert result.poll() == 0
    assert observer.acks == [] and result.verified_epoch is None


def test_page_progress_cannot_regress_or_skip_offered_events():
    engine = Engine()
    first = page([account_event(1)])
    observer = Observer([first, page([])], offered=0)
    value = pump(engine, observer)
    assert value.poll() == 1
    assert value.poll() == 0
    assert observer.acks == [1] and value.verified_epoch is None
    assert engine.gaps[-1] == "observation_progress_invalid"


@pytest.mark.parametrize("page_ack", [0, 2])
def test_unexpected_page_ack_change_is_not_treated_as_local_progress(page_ack):
    engine = Engine()
    second = page([account_event(2)])
    second.page.progress.acknowledged_sequence = page_ack
    observer = Observer([page([account_event(1)]), second])
    value = pump(engine, observer)
    assert value.poll() == 1 and value.verified_epoch == EPOCH
    assert value.poll() == 0 and observer.acks == [1] and value.verified_epoch is None
    assert engine.gaps[-1] == "observation_progress_invalid"


def test_page_exposing_more_offered_events_cannot_make_prefix_ready():
    engine = Engine()
    response = page([account_event(1)], has_more=False)
    response.page.progress.offered_sequence = 5
    observer = Observer([response], offered=0)
    value = pump(engine, observer)
    assert value.poll() == 1 and observer.acks == [1]
    assert value.verified_epoch is None and engine.gaps[-1] == "observation_replay_incomplete"


def test_ack_exposing_more_offered_events_cannot_make_prefix_ready():
    class AdvancedObserver(Observer):
        def acknowledge(self, sequence, *, expected_epoch, timeout_seconds):
            response = super().acknowledge(sequence, expected_epoch=expected_epoch, timeout_seconds=timeout_seconds)
            response.progress.offered_sequence = 5
            return response

    engine = Engine()
    observer = AdvancedObserver([page([account_event(1)])], offered=0)
    value = pump(engine, observer)
    assert value.poll() == 1 and observer.acks == [1]
    assert value.verified_epoch is None and engine.gaps[-1] == "observation_replay_incomplete"


def test_ack_offered_sequence_cannot_regress():
    class RegressedObserver(Observer):
        def acknowledge(self, sequence, *, expected_epoch, timeout_seconds):
            response = super().acknowledge(sequence, expected_epoch=expected_epoch, timeout_seconds=timeout_seconds)
            response.progress.offered_sequence = 1
            return response

    first = page([account_event(1)], has_more=True)
    first.page.progress.offered_sequence = 2
    engine = Engine()
    observer = RegressedObserver([first], offered=2)
    value = pump(engine, observer)
    assert value.poll() == 1 and observer.acks == [1] and value._after == 0
    assert value.verified_epoch is None and engine.gaps[-1] == "observation_ack_unproven"


@pytest.mark.parametrize("failure", ["read_exception", "missing_progress", "missing_events", "bad_has_more"])
def test_malformed_or_failed_read_revokes_previous_ready_state(failure):
    engine = Engine()
    observer = Observer([page([account_event(1)])])
    value = pump(engine, observer)
    assert value.poll() == 1 and value.verified_epoch == EPOCH
    if failure == "read_exception":
        def fail(**kwargs):
            raise RuntimeError("read failed")
        observer.read_after = fail
    else:
        response = page([])
        response.page.progress.offered_sequence = 1
        if failure == "missing_progress":
            del response.page.progress
        elif failure == "missing_events":
            del response.page.events
        else:
            response.page.has_more = "yes"
        observer.pages.append(response)
    assert value.poll() == 0 and value.verified_epoch is None
    assert observer.acks == [1]


@pytest.mark.parametrize("fault", ["wrong_epoch", "invalid_count", "invalid_covered", "future_last", "stale_revision"])
def test_unproven_apply_receipt_never_acks(fault):
    class BadReceiptEngine(Engine):
        def apply_observations_bound(self, events):
            receipt = super().apply_observations_bound(events)
            if fault == "wrong_epoch":
                return replace(receipt, epoch="f" * 64)
            if fault == "invalid_count":
                return replace(receipt, applied_count=receipt.applied_count + 1)
            if fault == "invalid_covered":
                return replace(receipt, covered_count=0)
            if fault == "future_last":
                return replace(receipt, last_sequence=receipt.last_sequence + 1)
            self.revision += 1  # same epoch and sequence, different committed revision
            return receipt

    engine = BadReceiptEngine()
    observer = Observer([page([account_event(1)])], offered=1)
    value = pump(engine, observer)
    value.poll()
    assert observer.acks == [] and value.verified_epoch is None
    assert engine.gaps[-1] == "observation_apply_unproven"


def test_new_epoch_never_acks_old_epoch_receipt():
    class OldEpochEngine(Engine):
        stale = False

        def apply_observations_bound(self, events):
            receipt = super().apply_observations_bound(events)
            return replace(receipt, epoch=EPOCH) if self.stale else receipt

    new_epoch = "f" * 64
    engine = OldEpochEngine()
    observer = Observer([page([account_event(1)]), page([account_event(1)], epoch=new_epoch)])
    value = pump(engine, observer)
    assert value.poll() == 1
    observer.epoch = new_epoch
    observer.acknowledged = 0
    assert value.poll() == 0 and value.verified_epoch is None
    observer.pages.append(page([account_event(1)], epoch=new_epoch))
    engine.stale = True
    assert value.poll() == 0 and observer.acks == [1]
    assert value.acknowledged == 0 and value.verified_epoch is None


def test_failed_reset_receipt_cannot_make_empty_epoch_ready():
    class BlockedReset(Engine):
        def reset_observations_bound(self, epoch, head):
            result = super().reset_observations_bound(epoch, head)
            return replace(result, blocked=True)

    engine, observer = BlockedReset(), Observer([])
    value = pump(engine, observer)
    assert value.poll() == 0 and observer.reads == [] and value.verified_epoch is None
    assert engine.gaps[-1] == "observation_reset_unproven"


@pytest.mark.parametrize("epoch", [None, "", "x" * 64, "A" * 64, "e" * 63])
def test_invalid_epoch_progress_never_resets_or_becomes_ready(epoch):
    engine, observer = Engine(), Observer([], epoch=epoch)
    value = pump(engine, observer)
    assert value.poll() == 0 and engine.resets == [] and observer.reads == []
    assert value.verified_epoch is None and value.last_code == "observation_invalid_progress"


def test_reset_progress_rejects_acknowledgement_beyond_offered():
    engine, observer = Engine(), Observer([])
    observer.progress = lambda **kw: SimpleNamespace(outcome="OBSERVED", code=None, progress=SimpleNamespace(
        epoch=EPOCH, offered_sequence=0, acknowledged_sequence=1,
    ))
    value = pump(engine, observer)
    assert value.poll() == 0 and engine.resets == [] and value.verified_epoch is None


def test_malformed_progress_never_resets_or_becomes_ready():
    engine, observer = Engine(), Observer([])
    observer.progress = lambda **kw: SimpleNamespace(outcome="OBSERVED", progress=object())
    value = pump(engine, observer)
    assert value.poll() == 0 and engine.resets == [] and observer.reads == []
    assert value.verified_epoch is None


@pytest.mark.parametrize("failure", ["denied", "unknown", "exception", "missing", "epoch", "lower", "higher", "offered"])
def test_unproven_ack_stops_and_marks_gap(failure):
    class AckObserver(Observer):
        def acknowledge(self, sequence, *, expected_epoch, timeout_seconds):
            self.acks.append(sequence)
            if failure == "exception":
                raise RuntimeError("ack lost")
            if failure == "denied":
                return SimpleNamespace(outcome="REQUEST_DENIED", code="CONSUMER_REPLAY_REQUIRED")
            if failure == "unknown":
                return SimpleNamespace(outcome="UNKNOWN")
            if failure == "missing":
                return SimpleNamespace(outcome="OBSERVED")
            return SimpleNamespace(outcome="OBSERVED", progress=SimpleNamespace(
                epoch="f" * 64 if failure == "epoch" else expected_epoch,
                acknowledged_sequence=0 if failure == "lower" else 2 if failure == "higher" else sequence,
                offered_sequence=0 if failure == "offered" else 2,
            ))

    engine = Engine()
    observer = AckObserver([page([account_event(1)])], offered=1)
    value = pump(engine, observer)
    assert value.poll() == 1
    assert observer.acks == [1] and value._after == 0 and value.verified_epoch is None
    assert engine.gaps[-1] in ("observation_ack_failed", "observation_ack_unproven")


def test_ack_may_not_exceed_validated_historical_source_watermark():
    class AheadObserver(Observer):
        def acknowledge(self, sequence, *, expected_epoch, timeout_seconds):
            self.acks.append(sequence)
            return SimpleNamespace(outcome="OBSERVED", progress=SimpleNamespace(
                epoch=expected_epoch, acknowledged_sequence=3, offered_sequence=3,
            ))

    first = page([account_event(1)], has_more=True)
    first.page.progress.offered_sequence = 2
    first.page.progress.acknowledged_sequence = 2
    engine = Engine()
    observer = AheadObserver([first], offered=2, acknowledged=2)
    value = pump(engine, observer)
    assert value.poll() == 1 and observer.acks == [1] and len(observer.reads) == 1
    assert value._after == 0 and value.verified_epoch is None
    assert engine.gaps[-1] == "observation_ack_unproven"


def test_lost_ack_replay_does_not_double_count():
    class LostOnce(Observer):
        def acknowledge(self, sequence, *, expected_epoch, timeout_seconds):
            if not self.acks:
                self.acks.append(sequence)
                return SimpleNamespace(outcome="UNKNOWN")
            return super().acknowledge(sequence, expected_epoch=expected_epoch, timeout_seconds=timeout_seconds)

    engine = Engine()
    observer = LostOnce([page([account_event(1)]), page([account_event(1)])], offered=1)
    value = pump(engine, observer)
    assert value.poll() == 1 and value.verified_epoch is None
    assert value.poll() == 0 and value.verified_epoch == EPOCH
    assert observer.acks == [1, 1]


def test_reentrant_poll_during_ack_marks_gap_even_if_ack_replied():
    holder = {}

    class ReentrantObserver(Observer):
        def acknowledge(self, sequence, *, expected_epoch, timeout_seconds):
            assert holder["pump"].poll() == 0
            return super().acknowledge(sequence, expected_epoch=expected_epoch, timeout_seconds=timeout_seconds)

    engine = Engine()
    observer = ReentrantObserver([page([account_event(1)])], offered=1)
    value = pump(engine, observer)
    holder["pump"] = value
    assert value.poll() == 1 and observer.acks == [1]
    assert value._after == 0 and value.verified_epoch is None


def test_cursor_reset_during_ack_marks_gap_after_ack_may_have_occurred():
    engine = Engine()

    class ResetDuringAck(Observer):
        def acknowledge(self, sequence, *, expected_epoch, timeout_seconds):
            response = super().acknowledge(sequence, expected_epoch=expected_epoch, timeout_seconds=timeout_seconds)
            engine.reset_observations(expected_epoch, 0)
            return response

    observer = ResetDuringAck([page([account_event(1)])], offered=1)
    value = pump(engine, observer)
    assert value.poll() == 1 and observer.acks == [1]
    # The external ACK was already invoked; the later check can only mark a
    # gap.  An owner-local/cross-instance ACK fence is still needed.
    assert value._after == 0 and value.verified_epoch is None
    assert engine.gaps[-1] == "observation_ack_unproven"


def test_real_risk_conflict_stops_before_collision_without_ack(service):
    engine = service._engine
    observer = Observer([page([account_event(1, key="same"), account_event(2, key="same")])], offered=2)
    value = pump(engine, observer)
    assert value.poll() == 1
    assert observer.acks == [] and value.verified_epoch is None
    with service._ledger.transaction() as txn:
        cursor = txn.cursor()
        assert cursor["last_sequence"] == 0 and cursor["continuity"] == "UNVERIFIED"
        assert txn.has_order_identity_conflict()


def test_real_risk_external_event_same_epoch_replay_is_covered_without_reprojection(service):
    engine = service._engine
    original = Observer([page([account_event(1, principal="external", key="external-order")])], offered=1)
    value = pump(engine, original)
    assert value.poll() == 1 and original.acks == [1] and value.verified_epoch == EPOCH
    with service._ledger.transaction() as txn:
        original_rows = [tuple(row) for row in txn._c.execute(
            "SELECT * FROM risk_pretrade_orders ORDER BY rowid"
        ).fetchall()]
        assert len(original_rows) == 1 and not txn.has_order_identity_conflict()
    replacement = Observer([page([account_event(1, principal="external", key="external-order")])],
                           offered=1, acknowledged=1)
    value.replace_observer(replacement)
    assert value.poll() == 0 and replacement.acks == [1] and value.verified_epoch == EPOCH
    with service._ledger.transaction() as txn:
        replayed_rows = [tuple(row) for row in txn._c.execute(
            "SELECT * FROM risk_pretrade_orders ORDER BY rowid"
        ).fetchall()]
        cursor = txn.cursor()
        assert not txn.has_order_identity_conflict()
        assert cursor["last_sequence"] == 0 and cursor["continuity"] == "VERIFIED"
    assert replayed_rows == original_rows


def test_real_risk_two_page_replay_accepts_known_historical_ack_without_skipping(service):
    first = account_event(1, principal="external", key="first")
    second = account_event(2, principal="external", key="second")
    engine = service._engine
    original = Observer([page([first, second])], offered=2)
    value = pump(engine, original)
    assert value.poll() == 2 and original.acks == [2]
    with service._ledger.transaction() as txn:
        old_rows = [tuple(row) for row in txn._c.execute(
            "SELECT * FROM risk_pretrade_orders ORDER BY rowid"
        ).fetchall()]
    first_page = page([first], has_more=True)
    first_page.page.progress.offered_sequence = 2
    first_page.page.progress.acknowledged_sequence = 2
    second_page = page([second])
    second_page.page.progress.acknowledged_sequence = 2
    replacement = Observer([first_page, second_page], offered=2, acknowledged=2)
    value.replace_observer(replacement)
    assert value.poll() == 0 and replacement.acks == [1, 2]
    assert [read["after_sequence"] for read in replacement.reads] == [0, 1]
    assert value._after == 2 and value.verified_epoch == EPOCH
    with service._ledger.transaction() as txn:
        assert txn.cursor()["last_sequence"] == 1 and not txn.has_order_identity_conflict()
        assert [tuple(row) for row in txn._c.execute(
            "SELECT * FROM risk_pretrade_orders ORDER BY rowid"
        ).fetchall()] == old_rows


def test_real_risk_changed_event_at_same_epoch_sequence_is_not_acknowledged(service):
    engine = service._engine
    original = Observer([page([account_event(1, principal="external", key="external-order")])], offered=1)
    value = pump(engine, original)
    assert value.poll() == 1 and original.acks == [1]
    with service._ledger.transaction() as txn:
        original_rows = [tuple(row) for row in txn._c.execute(
            "SELECT * FROM risk_pretrade_orders ORDER BY rowid"
        ).fetchall()]
    replacement = Observer([page([account_event(1, principal="external", key="external-order",
                                                quantity="2")])], offered=1, acknowledged=1)
    value.replace_observer(replacement)
    assert value.poll() == 0 and replacement.acks == [] and value.verified_epoch is None
    with service._ledger.transaction() as txn:
        assert txn.has_order_identity_conflict()
        assert txn.cursor()["continuity"] == "UNVERIFIED"
        assert [tuple(row) for row in txn._c.execute(
            "SELECT * FROM risk_pretrade_orders ORDER BY rowid"
        ).fetchall()] == original_rows


def test_real_risk_legacy_history_without_fingerprint_never_replays_or_acks(service):
    with service._ledger.transaction() as txn:
        txn._c.execute("INSERT INTO risk_pretrade_seen_events VALUES (?,?,?)", (txn._scope, EPOCH, 0))
    observer = Observer([page([account_event(1)])], offered=1)
    value = pump(service._engine, observer)
    assert value.poll() == 0 and observer.reads == [] and observer.acks == []
    assert value.verified_epoch is None
    with service._ledger.transaction() as txn:
        assert txn.has_order_identity_conflict() and txn.cursor()["continuity"] == "UNVERIFIED"
