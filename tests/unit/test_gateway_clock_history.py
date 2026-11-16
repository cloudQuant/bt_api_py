"""Real refresher worker publications with a controlled synthetic SNTP boundary."""
import queue
import threading
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace

import pytest

import bt_api_py.gateway_clock_history as history_module
import bt_api_py.gateway_server_clock_refresh as refresh_module
from bt_api_py.gateway_clock_history import GatewayClockHistoryError
from tests.unit.test_iter63_gateway_server_clock_refresh import make_refresher, wait_for


class ControlledQuery:
    def __init__(self):
        self.responses = queue.Queue()
        self.entered = threading.Event()
        self.second_entered = threading.Event()
        self.calls = 0

    def __call__(self, *_):
        self.entered.set()
        self.calls += 1
        if self.calls >= 2:
            self.second_entered.set()
        value = self.responses.get(timeout=5)
        if value == 'failure':
            raise OSError('synthetic private detail')
        return value, 100

    def send(self, value):
        self.responses.put(value)


def stop_and_release(source, query):
    if not source.stop(timeout_seconds=0):
        query.send(0)
        assert source.stop(timeout_seconds=2)


def test_real_worker_records_publication_failure_recovery_and_single_stop(monkeypatch):
    query = ControlledQuery()
    source, _ = make_refresher(monkeypatch, query, history_capacity=16)
    source.start()
    try:
        query.send(11)
        first = wait_for(source.current)
        first_page = source.read_history(after_sequence=0)
        assert first_page.available and len(first_page.records) == 1
        assert first_page.records[0].snapshot is first
        assert source.is_current(first)
        with pytest.raises(FrozenInstanceError):
            first_page.records[0].kind = 'STOPPED'
        query.send('failure')
        wait_for(lambda: source.read_history(after_sequence=0).last_sequence >= 2)
        assert source.current() is None and not source.is_current(first)
        query.send(12)
        recovered = wait_for(source.current)
        assert source.is_current(recovered) and not source.is_current(first)
        assert not source.is_current(replace(recovered))
        before_stop = source.read_history(after_sequence=0)
        assert [row.kind for row in before_stop.records] == ['SAMPLE', 'UNAVAILABLE', 'SAMPLE']
        assert before_stop.records[-1].snapshot is recovered
        assert all(row.observed_monotonic_ns >= row.snapshot.anchor_monotonic_ns
                   for row in before_stop.records if row.snapshot is not None)
        stop_and_release(source, query)
        after_stop = source.read_history(after_sequence=0)
        assert not after_stop.available and after_stop.records[-1].kind == 'STOPPED'
        assert after_stop.records[-1].snapshot is None
        assert source.stop(timeout_seconds=2)
        assert source.read_history(after_sequence=0) == after_stop
        old_epoch = after_stop.source_epoch
        source.start()
        query.send(13)
        wait_for(source.current)
        new_page = source.read_history(after_sequence=0)
        assert new_page.source_epoch != old_epoch and new_page.records[0].sequence == 1
    finally:
        stop_and_release(source, query)


def test_ring_paging_exact_gap_and_strict_cursor(monkeypatch):
    query = ControlledQuery()
    source, _ = make_refresher(monkeypatch, query, history_capacity=2)
    source.start()
    try:
        for number in range(1, 6):
            query.send(number)
            wait_for(lambda number=number: source.read_history(after_sequence=0).last_sequence >= number)
        page = source.read_history(after_sequence=0, limit=1)
        assert page.first_available_sequence == 4 and page.last_sequence == 5 and page.gap
        assert [row.sequence for row in page.records] == [4]
        page2 = source.read_history(after_sequence=4, limit=1)
        assert not page2.gap and [row.sequence for row in page2.records] == [5]
        assert not source.read_history(after_sequence=3).gap
        assert source.read_history(after_sequence=2).gap
        for after, limit in [(True, 1), (-1, 1), (6, 1), (0, True), (0, 0), (0, 257)]:
            with pytest.raises(GatewayClockHistoryError, match='CLOCK_HISTORY_INVALID_CURSOR'):
                source.read_history(after_sequence=after, limit=limit)
    finally:
        stop_and_release(source, query)


@pytest.mark.parametrize('bad_clock', ['regressed', 'zero', 'boolean', 'exception'])
def test_observer_clock_failure_does_not_change_publication(monkeypatch, bad_clock):
    query = ControlledQuery()
    source, _ = make_refresher(monkeypatch, query, history_capacity=8)
    source.start()
    try:
        query.send(1)
        first = wait_for(source.current)
        last = source.read_history(after_sequence=0).records[-1].observed_monotonic_ns
        def bad():
            if bad_clock == 'exception':
                raise RuntimeError('private observer failure')
            return {'regressed': last - 1, 'zero': 0, 'boolean': True}[bad_clock]
        monkeypatch.setattr(history_module, 'monotonic_ns', bad)
        query.send(2)
        second = wait_for(lambda: (value if (value := source.current()) is not first else None))
        assert source.is_current(second) and not source.is_current(first)
        page = source.read_history(after_sequence=0)
        assert not page.available and page.last_sequence == 1
        assert page.records[0].snapshot is first
        stop_and_release(source, query)
        assert source.read_history(after_sequence=0).last_sequence == 1
        assert source.current() is None
    finally:
        stop_and_release(source, query)


def test_history_config_owner_pid_and_disabled(monkeypatch):
    disabled, _ = make_refresher(monkeypatch, lambda *_: (0, 100))
    with pytest.raises(GatewayClockHistoryError, match='CLOCK_HISTORY_DISABLED'):
        disabled.read_history(after_sequence=0)
    for value in (True, -1, 1, 4097, 2.0):
        with pytest.raises(ValueError, match='INVALID_GATEWAY_SERVER_CLOCK'):
            make_refresher(monkeypatch, lambda *_: (0, 100), history_capacity=value)
    query = ControlledQuery()
    source, verifier = make_refresher(monkeypatch, query, history_capacity=4)
    source.start()
    try:
        query.send(1)
        sample = wait_for(source.current)
        assert source.read_history(after_sequence=0).available
        verifier._timeout += 1
        assert not source.read_history(after_sequence=0).available
        assert source.is_current(sample)
        errors = []
        def wrong_owner():
            try:
                source.read_history(after_sequence=0)
            except RuntimeError as exc:
                errors.append(str(exc))
        thread = threading.Thread(target=wrong_owner)
        thread.start()
        thread.join(1)
        assert errors == ['GATEWAY_CLOCK_OWNER_CHANGED']
        with monkeypatch.context() as context:
            context.setattr('bt_api_py.gateway_server_clock_refresh.os.getpid', lambda: source._pid + 1)
            with pytest.raises(RuntimeError, match='GATEWAY_CLOCK_OWNER_CHANGED'):
                source.read_history(after_sequence=0)
    finally:
        stop_and_release(source, query)


def test_stop_retry_preserves_worker_and_only_one_stop_record(monkeypatch):
    query = ControlledQuery()
    source, _ = make_refresher(monkeypatch, query, history_capacity=8)
    source.start()
    query.send(1)
    first = wait_for(source.current)
    worker = source._worker
    try:
        # The second native query remains in flight until the test releases it.
        assert query.second_entered.wait(2)
        assert source.stop(timeout_seconds=0) is False
        assert source._worker is worker and not source.is_current(first)
        assert [row.kind for row in source.read_history(after_sequence=0).records] == ['SAMPLE', 'STOPPED']
        query.send(2)
        assert source.stop(timeout_seconds=2)
        assert [row.kind for row in source.read_history(after_sequence=0).records] == ['SAMPLE', 'STOPPED']
    finally:
        stop_and_release(source, query)


def test_available_does_not_claim_sample_is_valid(monkeypatch):
    source, _ = make_refresher(monkeypatch, lambda *_: (0, 100), history_capacity=4,
                               max_sample_validity_seconds=.03, refresh_interval_seconds=1)
    source.start()
    try:
        sample = wait_for(source.current)
        wait_for(lambda: source.current() is None)
        assert not source.is_current(sample)
        assert source.read_history(after_sequence=0).available
    finally:
        assert source.stop(timeout_seconds=2)


def test_history_initialization_failure_does_not_block_clock(monkeypatch):
    def broken_history(_capacity):
        raise RuntimeError('private observer initialization error')
    monkeypatch.setattr(refresh_module, '_GatewayClockHistory', broken_history)
    source, _ = make_refresher(monkeypatch, lambda *_: (0, 100), history_capacity=4)
    source.start()
    try:
        sample = wait_for(source.current)
        assert source.is_current(sample)
        with pytest.raises(GatewayClockHistoryError, match='CLOCK_HISTORY_UNAVAILABLE'):
            source.read_history(after_sequence=0)
    finally:
        assert source.stop(timeout_seconds=2)


def test_stop_fences_delayed_publication_without_late_history(monkeypatch):
    query = ControlledQuery()
    source, _ = make_refresher(monkeypatch, query, history_capacity=8)
    entered, release = threading.Event(), threading.Event()
    original = source._sample_change

    @contextmanager
    def coordinated_change():
        if threading.current_thread() is not source._owner:
            entered.set()
            assert release.wait(3)
        with original():
            yield

    monkeypatch.setattr(source, '_sample_change', coordinated_change)
    source.start()
    try:
        query.send(1)
        assert entered.wait(2)
        assert not source.stop(timeout_seconds=0)
        before_release = source.read_history(after_sequence=0)
        assert [record.kind for record in before_release.records] == ['STOPPED']
        release.set()
        assert source.stop(timeout_seconds=2)
        assert source.current() is None
        assert source.read_history(after_sequence=0) == before_release
    finally:
        release.set()
        assert source.stop(timeout_seconds=2)


def test_history_read_exception_is_finite_and_does_not_change_clock(monkeypatch):
    query = ControlledQuery()
    source, _ = make_refresher(monkeypatch, query, history_capacity=4)
    source.start()
    try:
        query.send(1)
        sample = wait_for(source.current)
        def broken_read(*_args):
            raise RuntimeError('private history failure')
        with monkeypatch.context() as context:
            context.setattr(source._history, 'read', broken_read)
            with pytest.raises(GatewayClockHistoryError, match='CLOCK_HISTORY_UNAVAILABLE') as error:
                source.read_history(after_sequence=0)
            assert 'private' not in str(error.value)
        assert source.is_current(sample)
        page = source.read_history(after_sequence=0)
        assert not page.available and page.records[0].snapshot is sample
    finally:
        stop_and_release(source, query)


def test_publication_config_drift_is_sticky_after_aba_until_new_epoch(monkeypatch):
    query = ControlledQuery()
    source, verifier = make_refresher(monkeypatch, query, history_capacity=8)
    source.start()
    try:
        query.send(1)
        first = wait_for(source.current)
        before = source.read_history(after_sequence=0)
        original = verifier._validity_ns
        verifier._validity_ns = original // 2
        query.send(2)
        second = wait_for(lambda: (value if (value := source.current()) is not first else None))
        assert source.is_current(second) and not source.is_current(first)
        # No history read occurred while the mutated configuration was present.
        verifier._validity_ns = original
        page = source.read_history(after_sequence=0)
        assert not page.available and page.last_sequence == before.last_sequence == 1
        assert page.records == before.records and not page.gap
        assert source.is_current(second)
        stop_and_release(source, query)
        source.start()
        query.send(3)
        wait_for(source.current)
        restarted = source.read_history(after_sequence=0)
        assert restarted.available and restarted.source_epoch != before.source_epoch
        assert [record.sequence for record in restarted.records] == [1]
    finally:
        stop_and_release(source, query)


def test_read_observed_config_drift_is_sticky_after_aba(monkeypatch):
    query = ControlledQuery()
    source, verifier = make_refresher(monkeypatch, query, history_capacity=8)
    source.start()
    try:
        query.send(1)
        sample = wait_for(source.current)
        assert source.read_history(after_sequence=0).available
        original = verifier._timeout
        verifier._timeout = original + 1
        assert not source.read_history(after_sequence=0).available
        assert source.is_current(sample)
        verifier._timeout = original
        page = source.read_history(after_sequence=0)
        assert not page.available and page.records[0].snapshot is sample
        assert source.is_current(sample)
    finally:
        stop_and_release(source, query)
