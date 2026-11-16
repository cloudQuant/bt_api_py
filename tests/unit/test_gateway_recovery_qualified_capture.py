"""Owner-local page/clock pairing using a real qualifier and synthetic history."""
import os
import threading
import time
from dataclasses import FrozenInstanceError, replace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.market_recovery_observation import (
    RecoveryCoverageObservation,
    RecoveryObservationMember,
    RecoveryObservationPage,
)

from bt_api_py.gateway_recovery_clock_qualification import (
    GatewayRecoveryClockQualification,
    GatewayRecoveryClockQualifier,
)
from bt_api_py.gateway_recovery_qualified_capture import (
    GatewayQualifiedRecoveryCapture,
    GatewayQualifiedRecoveryCaptureError,
    GatewayQualifiedRecoveryPage,
)
from tests.unit.test_gateway_clock_history import ControlledQuery, stop_and_release
from tests.unit.test_gateway_recovery_clock_qualification import fake_history, point
from tests.unit.test_iter63_gateway_server_clock_refresh import make_refresher, wait_for


@pytest.fixture
def clock(monkeypatch):
    query = ControlledQuery()
    source, _ = make_refresher(monkeypatch, query, history_capacity=8)
    source.start()
    query.send(0)
    wait_for(source.current)
    base = time.monotonic_ns() - 10_000_000_000
    fake_history(monkeypatch, source, [point(base, 1, 100)])
    try:
        yield GatewayRecoveryClockQualifier(source, max_interval_ns=1_000_000_000), base, source
    finally:
        stop_and_release(source, query)


def observation(seq, base, *, state='RECOVERING', start=150, through=250, epoch='b' * 32,
                episode='c' * 32):
    instrument = MarketInstrumentKey('ctp', 'CFFEX', 'simnow', 'future', 'IF2612', 'IF2612')
    member = RecoveryObservationMember(MarketSubscriptionKey('source', 'entitlement', instrument, 'tick'), 'gen')
    reason = {'RECOVERING': 'BEGIN', 'RECOVERED': 'COMPLETED', 'FAILED': 'FAILED', 'UNKNOWN': 'CLOSED'}[state]
    return RecoveryCoverageObservation(epoch, os.getpid(), seq, episode, state, reason,
        base + max(through, 500), None if state == 'UNKNOWN' else base + start,
        None if state == 'UNKNOWN' else base + through, 1, 1, (member,))


def page(records, *, epoch='b' * 32, after=0):
    last = records[-1].sequence if records else 0
    return RecoveryObservationPage(epoch, os.getpid(), after, 1, last, tuple(records), False)


def test_real_qualifier_pairs_exact_coverage_zero_length_and_multiple_episodes(clock):
    qualifier, base, _ = clock
    raw = page([observation(1, base), observation(2, base, state='RECOVERED', start=300, through=300,
                                                episode='d' * 32)])
    calls = []
    def capture(after, limit):
        calls.append((after, limit))
        return raw
    result = GatewayQualifiedRecoveryCapture(capture, qualifier=qualifier).capture(0, 2)
    assert result.page is raw and calls == [(0, 2)]
    assert all(value.status == 'QUALIFIED' for value in result.qualifications)
    assert all(value.observation is item for value, item in zip(result.qualifications, raw.observations, strict=True))
    first, zero = result.qualifications
    assert first.start_utc_lower_ns == 1_700_000_000_000_000_000 + 150 - 100
    assert first.end_utc_upper_ns == 1_700_000_000_000_000_000 + 250 + 100
    assert zero.start_utc_lower_ns == zero.end_utc_lower_ns
    assert zero.start_utc_upper_ns == zero.end_utc_upper_ns
    assert (first.source_epoch, first.first_sample_sequence, first.last_sample_sequence) == ('a' * 32, 1, 1)
    with pytest.raises(FrozenInstanceError):
        result.qualifications = ()
    with pytest.raises(FrozenInstanceError):
        first.reason = 'other'


def test_source_unknown_no_clock_and_expiry_preserve_raw_record(clock):
    qualifier, base, _ = clock
    raw = page([observation(1, base, state='UNKNOWN'),
                observation(2, base, start=150, through=250),
                observation(3, base, start=1100, through=1200)])
    without_clock = GatewayQualifiedRecoveryCapture(lambda *_: raw).capture(0, 3)
    assert [item.reason for item in without_clock.qualifications] == [
        'SOURCE_UNKNOWN', 'CLOCK_UNAVAILABLE', 'CLOCK_UNAVAILABLE']
    paired = GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=qualifier).capture(0, 3)
    assert [item.status for item in paired.qualifications] == ['UNKNOWN', 'QUALIFIED', 'UNKNOWN']
    assert paired.qualifications[-1].reason == 'SAMPLE_EXPIRED'
    assert all(item.observation is obs for item, obs in zip(paired.qualifications, raw.observations, strict=True))
    assert paired.qualifications[0].source_epoch is None
    assert paired.qualifications[-1].end_utc_lower_ns is None


def test_lost_clock_history_gap_results_unknown(clock, monkeypatch):
    qualifier, base, source = clock
    fake_history(monkeypatch, source, [point(base, 3, 100)])
    raw = page([observation(1, base)])
    result = GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=qualifier).capture(0, 1)
    assert result.qualifications[0].reason == 'HISTORY_GAP'
    assert result.qualifications[0].start_utc_lower_ns is None


def test_qualifier_fault_downgrades_entire_page_without_exception_details(clock, monkeypatch):
    qualifier, base, _ = clock
    raw = page([observation(1, base), observation(2, base), observation(3, base, state='UNKNOWN')])
    original = qualifier.qualify
    calls = []
    def failing(start, end):
        calls.append((start, end))
        if len(calls) == 2:
            raise RuntimeError('private failure')
        return original(start, end)
    monkeypatch.setattr(qualifier, 'qualify', failing)
    result = GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=qualifier).capture(0, 3)
    assert [item.reason for item in result.qualifications] == ['HISTORY_UNAVAILABLE', 'HISTORY_UNAVAILABLE', 'SOURCE_UNKNOWN']
    assert all(item.start_utc_lower_ns is None for item in result.qualifications)
    assert len(calls) == 2


def test_invalid_qualifier_result_and_binding_change_discard_all_projections(clock, monkeypatch):
    qualifier, base, _ = clock
    raw = page([observation(1, base), observation(2, base)])
    original = qualifier.qualify
    calls = []
    def malformed(start, end):
        calls.append(1)
        return original(start, end) if len(calls) == 1 else GatewayRecoveryClockQualification(
            'UNKNOWN', 'HISTORY_GAP', start_utc_lower_ns=1)
    monkeypatch.setattr(qualifier, 'qualify', malformed)
    result = GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=qualifier).capture(0, 2)
    assert [item.reason for item in result.qualifications] == ['HISTORY_UNAVAILABLE'] * 2
    helper = GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=qualifier)
    def change_binding(start, end):
        result = original(start, end)
        helper._qualifier = None
        return result
    monkeypatch.setattr(qualifier, 'qualify', change_binding)
    result = helper.capture(0, 2)
    assert [item.reason for item in result.qualifications] == ['OWNER_CHANGED'] * 2
    assert all(item.start_utc_lower_ns is None for item in result.qualifications)


def test_page_constructor_enforces_exact_order_identity_and_shape(clock):
    qualifier, base, _ = clock
    raw = page([observation(1, base), observation(2, base)])
    result = GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=qualifier).capture(0, 2)
    first, second = result.qualifications
    with pytest.raises(ValueError, match='INVALID_QUALIFIED_RECOVERY_PAGE'):
        GatewayQualifiedRecoveryPage(raw, (second, first))
    with pytest.raises(ValueError, match='INVALID_QUALIFIED_RECOVERY_PAGE'):
        GatewayQualifiedRecoveryPage(raw, (first,))
    with pytest.raises(ValueError, match='INVALID_QUALIFIED_RECOVERY_PAGE'):
        GatewayQualifiedRecoveryPage(raw, (first, second, first))
    cloned = replace(raw.observations[0])
    cloned_qualification = replace(first, observation=cloned)
    assert cloned == raw.observations[0] and cloned is not raw.observations[0]
    with pytest.raises(ValueError, match='INVALID_QUALIFIED_RECOVERY_PAGE'):
        GatewayQualifiedRecoveryPage(raw, (cloned_qualification, second))
    object.__setattr__(first, 'reason', 'INVALID')
    with pytest.raises(ValueError, match='INVALID_QUALIFIED_RECOVERY_PAGE'):
        GatewayQualifiedRecoveryPage(raw, (first, second))


def test_finite_owner_pid_input_and_capture_errors(clock, monkeypatch):
    qualifier, base, _ = clock
    raw = page([observation(1, base)])
    helper = GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=qualifier)
    for after, limit in [(True, 1), (-1, 1), (1 << 63, 1), (0, True), (0, 0), (0, 257)]:
        with pytest.raises(GatewayQualifiedRecoveryCaptureError, match='CAPTURE_INVALID_ARGUMENT'):
            helper.capture(after, limit)
    errors = []
    def wrong_thread():
        try:
            helper.capture(0, 1)
        except GatewayQualifiedRecoveryCaptureError as error:
            errors.append(error.code)
    thread = threading.Thread(target=wrong_thread)
    thread.start()
    thread.join(1)
    assert errors == ['CAPTURE_OWNER_CHANGED']
    with monkeypatch.context() as context:
        context.setattr('bt_api_py.gateway_recovery_qualified_capture.os.getpid', lambda: helper._pid + 1)
        with pytest.raises(GatewayQualifiedRecoveryCaptureError, match='CAPTURE_OWNER_CHANGED'):
            helper.capture(0, 1)
    def broken(*_):
        raise RuntimeError('secret details')
    with pytest.raises(GatewayQualifiedRecoveryCaptureError, match='CAPTURE_FAILED') as error:
        GatewayQualifiedRecoveryCapture(broken).capture(0, 1)
    assert 'secret' not in str(error.value)
    helper._capture = lambda *_: raw
    with pytest.raises(GatewayQualifiedRecoveryCaptureError, match='CAPTURE_BINDING_CHANGED'):
        helper.capture(0, 1)


def test_source_page_scope_cursor_limit_and_epoch_rejected(clock):
    _, base, _ = clock
    raw = page([observation(1, base), observation(2, base)])
    with pytest.raises(GatewayQualifiedRecoveryCaptureError, match='CAPTURE_INVALID_PAGE'):
        GatewayQualifiedRecoveryCapture(lambda *_: raw).capture(0, 1)
    with pytest.raises(GatewayQualifiedRecoveryCaptureError, match='CAPTURE_INVALID_PAGE'):
        GatewayQualifiedRecoveryCapture(lambda *_: replace(raw, owner_pid=os.getpid() + 1,
            observations=tuple(replace(row, owner_pid=os.getpid() + 1) for row in raw.observations))).capture(0, 2)
    with pytest.raises(GatewayQualifiedRecoveryCaptureError, match='CAPTURE_INVALID_PAGE'):
        GatewayQualifiedRecoveryCapture(lambda *_: object()).capture(0, 2)
    first = page([])
    other = page([], epoch='d' * 32)
    pages = iter((first, other))
    capture = GatewayQualifiedRecoveryCapture(lambda *_: next(pages))
    assert capture.capture(0, 1).page is first
    with pytest.raises(GatewayQualifiedRecoveryCaptureError, match='CAPTURE_SOURCE_CHANGED'):
        capture.capture(0, 1)


def test_pending_pair_is_immutable_and_does_not_recompute(clock, monkeypatch):
    qualifier, base, _ = clock
    raw = page([observation(1, base)])
    calls = []
    original = qualifier.qualify
    def count(start, end):
        calls.append((start, end))
        return original(start, end)
    monkeypatch.setattr(qualifier, 'qualify', count)
    paired = GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=qualifier).capture(0, 1)
    for _ in range(3):
        paired.__post_init__()  # A future retry uses this same object, not a new capture.
    assert len(calls) == 1 and paired.qualifications[0].status == 'QUALIFIED'


def test_empty_poll_can_recapture_same_cursor_and_raw_gap_is_preserved(clock):
    qualifier, base, _ = clock
    empty = page([])
    row = observation(3, base)
    gapped = RecoveryObservationPage('b' * 32, os.getpid(), 0, 3, 3, (row,), True)
    pages = iter((empty, gapped))
    helper = GatewayQualifiedRecoveryCapture(lambda *_: next(pages), qualifier=qualifier)
    assert helper.capture(0, 5).qualifications == ()
    result = helper.capture(0, 5)
    assert result.page is gapped and result.page.gap is True
    assert result.qualifications[0].observation is row
    assert result.qualifications[0].status == 'QUALIFIED'


def test_qualifier_source_replacement_and_wrong_constructor_owner_fail_closed(clock):
    qualifier, base, _ = clock
    raw = page([observation(1, base)])
    helper = GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=qualifier)
    original = qualifier._source
    try:
        qualifier._source = object()
        result = helper.capture(0, 1)
        assert result.qualifications[0].reason == 'OWNER_CHANGED'
        assert result.qualifications[0].start_utc_lower_ns is None
        with pytest.raises(ValueError, match='INVALID_QUALIFIED_CAPTURE_CONFIGURATION'):
            GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=qualifier)
    finally:
        qualifier._source = original
    with pytest.raises(ValueError, match='INVALID_QUALIFIED_CAPTURE_CONFIGURATION'):
        GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=object())


def test_qualifier_max_interval_mutation_is_owner_changed(clock):
    qualifier, base, _ = clock
    raw = page([observation(1, base)])
    helper = GatewayQualifiedRecoveryCapture(lambda *_: raw, qualifier=qualifier)
    qualifier._max_interval_ns += 1
    result = helper.capture(0, 1)
    assert result.qualifications[0].reason == 'OWNER_CHANGED'
    assert result.qualifications[0].start_utc_lower_ns is None


def test_cursor_mismatch_and_mutated_nested_page_are_rejected(clock):
    _, base, _ = clock
    row = observation(2, base)
    raw = RecoveryObservationPage('b' * 32, os.getpid(), 1, 1, 2, (row,), False)
    with pytest.raises(GatewayQualifiedRecoveryCaptureError, match='CAPTURE_INVALID_PAGE'):
        GatewayQualifiedRecoveryCapture(lambda *_: raw).capture(0, 2)
    row = observation(1, base)
    raw = page([row])
    object.__setattr__(row.members[0].key.instrument, 'provider', '')
    with pytest.raises(GatewayQualifiedRecoveryCaptureError, match='CAPTURE_INVALID_PAGE'):
        GatewayQualifiedRecoveryCapture(lambda *_: raw).capture(0, 1)
