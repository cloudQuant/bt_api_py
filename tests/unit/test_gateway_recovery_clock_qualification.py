"""Local bounded-history qualification without real network or accounts."""
import os
import threading
import time
from dataclasses import FrozenInstanceError, replace

import pytest

from bt_api_py.clock_verification import ClockEvidence
from bt_api_py.gateway_clock_history import GatewayClockHistoryPage, GatewayClockHistoryRecord
from bt_api_py.gateway_recovery_clock_qualification import GatewayRecoveryClockQualifier
from bt_api_py.gateway_server_clock_refresh import GatewayServerClockSnapshot
from tests.unit.test_gateway_clock_history import ControlledQuery, stop_and_release
from tests.unit.test_iter63_gateway_server_clock_refresh import make_refresher, wait_for


@pytest.fixture
def live(monkeypatch):
    query = ControlledQuery()
    source, verifier = make_refresher(monkeypatch, query, history_capacity=16)
    source.start()
    query.send(1)
    wait_for(source.current)
    try:
        yield source, verifier, query
    finally:
        stop_and_release(source, query)


def point(base, seq, delta, *, offset=0, error=100, expiry=1000, kind='SAMPLE'):
    pub = base + delta
    if kind != 'SAMPLE':
        return GatewayClockHistoryRecord(seq, kind, pub, None)
    evidence = ClockEvidence(offset, error, 'trusted', 'sntp', base, base + expiry)
    snapshot = GatewayServerClockSnapshot(evidence, 1_700_000_000_000_000_000,
                                           base, os.getpid())
    return GatewayClockHistoryRecord(seq, kind, pub, snapshot)


def fake_history(monkeypatch, source, records, *, available=True, epoch='a' * 32):
    first = records[0].sequence if records else 1
    last = records[-1].sequence if records else 0
    def read(*, after_sequence, limit):
        return GatewayClockHistoryPage(epoch, os.getpid(), after_sequence, first, last,
            tuple(row for row in records if row.sequence > after_sequence)[:limit],
            after_sequence < first - 1, available)
    monkeypatch.setattr(source, 'read_history', read)


def qualifier(source):
    return GatewayRecoveryClockQualifier(source, max_interval_ns=1_000_000_000)


def test_real_refresher_publications_qualify_past_interval(monkeypatch):
    class QueryWithClockReadUncertainty(ControlledQuery):
        def __call__(self, *args):
            offset, _ = super().__call__(*args)
            # Real wall/monotonic calls in this test have scheduling jitter.
            return offset, 2_000_000

    query = QueryWithClockReadUncertainty()
    source, _ = make_refresher(monkeypatch, query, history_capacity=16)
    source.start()
    try:
        query.send(1)
        wait_for(source.current)
        first = source.read_history(after_sequence=0).records[0]
        query.send(2)
        second = wait_for(lambda: (page.records[-1] if (page := source.read_history(after_sequence=0)).last_sequence >= 2 else None))
        result = qualifier(source).qualify(first.observed_monotonic_ns, second.observed_monotonic_ns)
        assert result.status == 'QUALIFIED' and result.reason == 'OK', result
        assert result.start_utc_lower_ns <= result.start_utc_upper_ns
        assert result.end_utc_lower_ns <= result.end_utc_upper_ns
        with pytest.raises(FrozenInstanceError):
            result.status = 'UNKNOWN'
    finally:
        stop_and_release(source, query)


def test_two_samples_expired_now_are_qualified_using_published_coverage(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    records = [point(base, 1, 100, error=200), point(base, 2, 300, offset=20, error=200)]
    fake_history(monkeypatch, source, records)
    result = qualifier(source).qualify(base + 150, base + 500)
    assert result.status == 'QUALIFIED'
    assert (result.source_epoch, result.first_sample_sequence, result.last_sample_sequence) == ('a' * 32, 1, 2)
    later = qualifier(source).qualify(base + 350, base + 500)
    assert later.status == 'QUALIFIED'
    assert (later.first_sample_sequence, later.last_sample_sequence) == (1, 2)
    assert (result.start_utc_lower_ns, result.start_utc_upper_ns) == (
        1_700_000_000_000_000_000 + 150 - 200, 1_700_000_000_000_000_000 + 150 + 200)
    assert (result.end_utc_lower_ns, result.end_utc_upper_ns) == (
        1_700_000_000_000_000_000 + 520 - 200, 1_700_000_000_000_000_000 + 520 + 200)


@pytest.mark.parametrize('case', ['expiry', 'gap_before_start', 'clock_discontinuity', 'late_sample'])
def test_refuses_uncovered_or_discontinuous_intervals(live, monkeypatch, case):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    first = point(base, 1, 100, expiry=200 if case == 'expiry' else 1000)
    second = point(base, 2, 300, offset=1000 if case == 'clock_discontinuity' else 0)
    fake_history(monkeypatch, source, [first, second])
    start = base + (50 if case == 'late_sample' else 150)
    end = base + (250 if case == 'gap_before_start' else 500)
    if case == 'gap_before_start':
        # An expired first sample cannot be replaced before the second publication.
        fake_history(monkeypatch, source, [point(base, 1, 100, expiry=200), second])
    result = qualifier(source).qualify(start, end)
    assert result.status == 'UNKNOWN'
    assert result.start_utc_lower_ns is result.end_utc_upper_ns is None


@pytest.mark.parametrize('kind', ['UNAVAILABLE', 'STOPPED'])
def test_unavailable_events_block_only_intersecting_interval(live, monkeypatch, kind):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    records = [point(base, 1, 100), point(base, 2, 300, kind=kind), point(base, 3, 500)]
    fake_history(monkeypatch, source, records)
    source_qualifier = qualifier(source)
    assert source_qualifier.qualify(base + 150, base + 300).status == 'UNKNOWN'
    assert source_qualifier.qualify(base + 300, base + 550).status == 'UNKNOWN'
    assert source_qualifier.qualify(base + 150, base + 250).status == 'QUALIFIED'
    assert source_qualifier.qualify(base + 550, base + 600).status == 'QUALIFIED'


def test_prefix_loss_and_missing_interior_are_not_hidden(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    rows = [point(base, 3, 300), point(base, 4, 400)]
    fake_history(monkeypatch, source, rows)
    assert qualifier(source).qualify(base + 299, base + 350).status == 'UNKNOWN'
    assert qualifier(source).qualify(base + 300, base + 350).status == 'UNKNOWN'
    assert qualifier(source).qualify(base + 301, base + 450).status == 'UNKNOWN'
    fake_history(monkeypatch, source, [point(base, 1, 100), point(base, 3, 300)])
    assert qualifier(source).qualify(base + 150, base + 350).status == 'UNKNOWN'


def test_invalid_intervals_zero_length_and_future(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    fake_history(monkeypatch, source, [point(base, 1, 100, error=50)])
    source_qualifier = qualifier(source)
    result = source_qualifier.qualify(base + 200, base + 200)
    assert result.status == 'QUALIFIED'
    assert result.start_utc_lower_ns == result.end_utc_lower_ns
    assert result.start_utc_upper_ns - result.start_utc_lower_ns == 100
    for start, end in [(True, 1), (-1, 1), (2, 1), (0, 1_000_000_001), (0, float('inf'))]:
        assert source_qualifier.qualify(start, end).reason == 'INVALID_INTERVAL'
    now = time.monotonic_ns()
    assert source_qualifier.qualify(now + 1_000_000_000, now + 1_000_000_000).reason == 'INTERVAL_NOT_PAST'


def test_unavailable_config_and_owner_boundaries(live, monkeypatch):
    source, verifier, _ = live
    page = source.read_history(after_sequence=0)
    start = page.records[0].observed_monotonic_ns
    q = qualifier(source)
    assert q.qualify(start, start).status == 'QUALIFIED'
    results = []
    thread = threading.Thread(target=lambda: results.append(q.qualify(start, start)))
    thread.start()
    thread.join(1)
    assert results[0].reason == 'OWNER_CHANGED'
    with monkeypatch.context() as context:
        context.setattr('bt_api_py.gateway_recovery_clock_qualification.os.getpid', lambda: -1)
        assert q.qualify(start, start).reason == 'OWNER_CHANGED'
    verifier._timeout += 1
    assert q.qualify(start, start).status == 'UNKNOWN'


def test_clock_seam_at_interval_start_and_uncertainty_overlap(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    fake_history(monkeypatch, source, [point(base, 1, 100, error=10),
                                      point(base, 2, 200, offset=21, error=10)])
    assert qualifier(source).qualify(base + 200, base + 250).reason == 'CLOCK_DISCONTINUITY'
    fake_history(monkeypatch, source, [point(base, 1, 100, error=10),
                                      point(base, 2, 200, offset=20, error=10)])
    assert qualifier(source).qualify(base + 200, base + 250).status == 'QUALIFIED'


def test_fixed_high_water_and_epoch_change_are_checked(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    rows = [point(base, seq, 100 + seq, expiry=2000) for seq in range(1, 301)]
    calls = []
    def read(*, after_sequence, limit):
        calls.append(after_sequence)
        extra = point(base, 301, 1500, offset=1000, expiry=2000)
        current = rows if len(calls) == 1 else [*rows, extra]
        return GatewayClockHistoryPage('a' * 32, os.getpid(), after_sequence, 1,
            len(current), tuple(row for row in current if row.sequence > after_sequence)[:limit], False, True)
    monkeypatch.setattr(source, 'read_history', read)
    assert qualifier(source).qualify(base + 450, base + 500).status == 'QUALIFIED'
    assert calls == [0, 256, 300, 300]
    calls.clear()
    def changed(*, after_sequence, limit):
        result = read(after_sequence=after_sequence, limit=limit)
        return replace(result, source_epoch='b' * 32) if len(calls) > 1 else result
    monkeypatch.setattr(source, 'read_history', changed)
    assert qualifier(source).qualify(base + 450, base + 500).status == 'UNKNOWN'


@pytest.mark.parametrize('change', [
    {'pid': True}, {'anchor_wall_ns': True}, {'anchor_monotonic_ns': True},
    {'method': 'local'}, {'server': 'untrusted'}, {'offset_ns': True},
    {'error_bound_ns': True}, {'error_bound_ns': -1}, {'error_bound_ns': 5_000_001},
    {'valid_until_monotonic_ns': 'invalid'}, {'measured_monotonic_ns': True},
    {'measured_after_anchor': True}, {'expiry_too_late': True},
])
def test_invalid_snapshot_evidence_never_qualifies(live, monkeypatch, change):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    record = point(base, 1, 100)
    snapshot = record.snapshot
    evidence = snapshot.evidence
    if 'measured_after_anchor' in change:
        evidence = replace(evidence, measured_monotonic_ns=base + 1)
    elif 'expiry_too_late' in change:
        evidence = replace(evidence, valid_until_monotonic_ns=base + 2_000_000_000)
    elif set(change) <= {'pid', 'anchor_wall_ns', 'anchor_monotonic_ns'}:
        snapshot = replace(snapshot, **change)
    else:
        evidence = replace(evidence, **change)
    snapshot = replace(snapshot, evidence=evidence)
    fake_history(monkeypatch, source, [replace(record, snapshot=snapshot)])
    assert qualifier(source).qualify(base + 150, base + 200).status == 'UNKNOWN'


def test_unavailable_history_and_wrong_owner_cannot_be_supplied(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    fake_history(monkeypatch, source, [point(base, 1, 100)], available=False)
    assert qualifier(source).qualify(base + 150, base + 200).status == 'UNKNOWN'
    with pytest.raises(ValueError, match='INVALID_CLOCK_QUALIFIER_CONFIGURATION'):
        GatewayRecoveryClockQualifier(object(), max_interval_ns=100)
    with pytest.raises(ValueError, match='INVALID_CLOCK_QUALIFIER_CONFIGURATION'):
        GatewayRecoveryClockQualifier(source, max_interval_ns=True)


def test_paging_eviction_and_owner_pid_change_fail_closed(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    rows = [point(base, seq, 100 + seq, expiry=2000) for seq in range(1, 301)]
    calls = []
    def read(*, after_sequence, limit):
        calls.append(after_sequence)
        if len(calls) == 1:
            return GatewayClockHistoryPage('a' * 32, os.getpid(), 0, 1, 300,
                                             tuple(rows[:limit]), False, True)
        # The next needed sequence has already been overwritten.
        return GatewayClockHistoryPage('a' * 32, os.getpid(), after_sequence, 270, 300,
            tuple(row for row in rows if row.sequence >= 270)[:limit], True, True)
    monkeypatch.setattr(source, 'read_history', read)
    assert qualifier(source).qualify(base + 450, base + 500).status == 'UNKNOWN'
    def wrong_pid(*, after_sequence, limit):
        return GatewayClockHistoryPage('a' * 32, os.getpid() + 1, after_sequence, 1, 300,
            tuple(row for row in rows if row.sequence > after_sequence)[:limit], False, True)
    monkeypatch.setattr(source, 'read_history', wrong_pid)
    assert qualifier(source).qualify(base + 450, base + 500).status == 'UNKNOWN'


def test_local_monotonic_regression_or_future_publication_fails(live, monkeypatch):
    import bt_api_py.gateway_recovery_clock_qualification as qualification
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    fake_history(monkeypatch, source, [point(base, 1, 100)])
    clock = iter((base + 500, base + 499))
    monkeypatch.setattr(qualification, 'monotonic_ns', lambda: next(clock))
    assert qualifier(source).qualify(base + 150, base + 200).reason == 'CLOCK_DISCONTINUITY'
    # The first read cannot have observed a publication later than its own end.
    monkeypatch.setattr(qualification, 'monotonic_ns', lambda: base + 50)
    assert qualifier(source).qualify(base + 1, base + 2).reason == 'INVALID_HISTORY'


def test_late_unavailability_during_calculation_fails(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    record = point(base, 1, 100)
    calls = []
    def read(*, after_sequence, limit):
        calls.append(after_sequence)
        return GatewayClockHistoryPage('a' * 32, os.getpid(), after_sequence, 1, 1,
            (record,) if after_sequence == 0 else (), False, len(calls) < 3)
    monkeypatch.setattr(source, 'read_history', read)
    assert qualifier(source).qualify(base + 150, base + 200).status == 'UNKNOWN'
    assert calls == [0, 1, 1]


def test_expiry_is_exclusive_for_start_end_and_zero_length(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    fake_history(monkeypatch, source, [point(base, 1, 100, expiry=200)])
    q = qualifier(source)
    assert q.qualify(base + 199, base + 199).status == 'QUALIFIED'
    assert q.qualify(base + 200, base + 200).status == 'UNKNOWN'
    assert q.qualify(base + 150, base + 200).status == 'UNKNOWN'


def test_outside_interval_invalid_publication_does_not_poison_valid_period(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    expired = point(base, 1, 100, expiry=100)
    good = point(base, 2, 200, expiry=500)
    late = point(base, 3, 600, expiry=600)
    fake_history(monkeypatch, source, [expired, good, late])
    q = qualifier(source)
    result = q.qualify(base + 200, base + 450)
    assert result.status == 'QUALIFIED'
    assert result.first_sample_sequence == result.last_sample_sequence == 2
    assert q.qualify(base + 100, base + 150).status == 'UNKNOWN'
    assert q.qualify(base + 450, base + 600).status == 'UNKNOWN'


def test_unavailable_and_sample_at_same_start_is_unknown(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    records = [point(base, 1, 100), point(base, 2, 200, kind='UNAVAILABLE'),
               point(base, 3, 200)]
    fake_history(monkeypatch, source, records)
    assert qualifier(source).qualify(base + 200, base + 250).status == 'UNKNOWN'


def test_all_overlapping_calibrations_require_common_intersection(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    records = [point(base, 1, 100, offset=0, error=100),
               point(base, 2, 200, offset=200, error=100),
               point(base, 3, 300, offset=400, error=100)]
    fake_history(monkeypatch, source, records)
    q = qualifier(source)
    assert q.qualify(base + 150, base + 350).reason == 'CLOCK_DISCONTINUITY'
    # Even when the third publication precedes the interval, all three still
    # constrain the same continuous period at its starting instant.
    assert q.qualify(base + 350, base + 375).reason == 'CLOCK_DISCONTINUITY'


def test_expired_or_interrupted_old_calibration_does_not_constrain_new_period(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    old = point(base, 1, 100, offset=0, error=100, expiry=250)
    new = point(base, 2, 300, offset=1000, error=100)
    fake_history(monkeypatch, source, [old, new])
    assert qualifier(source).qualify(base + 300, base + 350).status == 'QUALIFIED'
    assert qualifier(source).qualify(base + 200, base + 350).status == 'UNKNOWN'
    interrupted = [point(base, 1, 100, offset=0, error=100),
                   point(base, 2, 200, kind='UNAVAILABLE'),
                   point(base, 3, 300, offset=1000, error=100)]
    fake_history(monkeypatch, source, interrupted)
    assert qualifier(source).qualify(base + 300, base + 350).status == 'QUALIFIED'


def test_lost_prefix_may_hide_unavailable_at_first_sample_instant(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    full = [point(base, 1, 200, kind='UNAVAILABLE'), point(base, 2, 200), point(base, 3, 300)]
    fake_history(monkeypatch, source, full)
    assert qualifier(source).qualify(base + 200, base + 250).status == 'UNKNOWN'
    fake_history(monkeypatch, source, full[1:])
    assert qualifier(source).qualify(base + 200, base + 250).status == 'UNKNOWN'
    assert qualifier(source).qualify(base + 201, base + 250).status == 'UNKNOWN'


def test_lost_prefix_can_hide_still_valid_conflicting_sample(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    full = [point(base, 1, 100, offset=0, error=100),
            point(base, 2, 200, offset=400, error=100)]
    fake_history(monkeypatch, source, full)
    assert qualifier(source).qualify(base + 250, base + 350).reason == 'CLOCK_DISCONTINUITY'
    fake_history(monkeypatch, source, full[1:])
    assert qualifier(source).qualify(base + 250, base + 350).reason == 'HISTORY_GAP'


def test_lost_prefix_expiry_upper_bound_is_half_open(live, monkeypatch):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    max_validity = source._max_validity_ns
    records = [point(base, 2, 100, expiry=200),
               point(base + max_validity, 3, 99)]
    fake_history(monkeypatch, source, records)
    bound = base + 100 + max_validity
    q = qualifier(source)
    assert q.qualify(bound - 1, bound + 10).reason == 'HISTORY_GAP'
    assert q.qualify(bound, bound + 10).status == 'QUALIFIED'


@pytest.mark.parametrize('kind', ['UNAVAILABLE', 'INVALID'])
def test_lost_prefix_explicit_reset_and_new_sample_clear_old_constraints(live, monkeypatch, kind):
    source, _, _ = live
    base = time.monotonic_ns() - 10_000_000_000
    reset = (point(base, 2, 200, kind='UNAVAILABLE') if kind == 'UNAVAILABLE'
             else point(base, 2, 200, expiry=200))
    records = [reset, point(base, 3, 300, offset=1000)]
    fake_history(monkeypatch, source, records)
    result = qualifier(source).qualify(base + 300, base + 350)
    assert result.status == 'QUALIFIED'
    assert result.first_sample_sequence == result.last_sample_sequence == 3
