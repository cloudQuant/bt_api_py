import os
import threading

import pytest
from bt_api_base.market_recovery_observation import (
    RecoveryCoverageObservation,
    RecoveryObservationPage,
)
from bt_api_gateway.recovery_observation_journal import RecoveryObservationJournal

import bt_api_py.gateway_recovery_observation as module
from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from tests.test_iter63_gateway_direct_owner import Harness, wait_for


def settings(tmp_path, **changes):
    values = {'journal_path': tmp_path / 'recovery.sqlite', 'scope_ref': 'account', 'source_ref': 'source',
                  'source_capacity': 4, 'page_limit': 4, 'max_events': 20, 'max_bytes': 100000,
                  'sqlite_timeout_seconds': 0.1, 'poll_interval_seconds': 0.01,
                  'owner_wait_seconds': 0.02, 'close_timeout_seconds': 0.1}
    values.update(changes)
    return module.GatewayRecoveryObservationSettings(**values)


def page(after):
    epoch = 'a' * 32
    item = RecoveryCoverageObservation(epoch, os.getpid(), 1, None, 'UNKNOWN', 'CLOSED',
                                       1, None, None, None, None, ())
    return RecoveryObservationPage(epoch, os.getpid(), after, 1, 1,
                                   (item,) if after == 0 else (), False)


def owner_for(tmp_path):
    harness = Harness(tmp_path)
    owner = GatewayDirectOwner(harness.factory, queue_size=4, max_queue_wait_seconds=2,
                               busy_timeout_seconds=2)
    assert owner.start(2)
    return owner, harness


def test_capture_owner_and_database_worker_are_distinct_and_commit_reply_retry(tmp_path, monkeypatch):
    owner, harness = owner_for(tmp_path)
    entered, release = threading.Event(), threading.Event()
    captures, writes = [], []
    original = module.RecoveryObservationJournal

    class Journal(original):
        def append_page(self, source, p):
            writes.append((threading.get_ident(), p))
            result = super().append_page(source, p)
            if len(writes) == 1:
                entered.set()
                assert release.wait(3)
                raise OSError('lost commit reply')
            return result

    monkeypatch.setattr(module, 'RecoveryObservationJournal', Journal)
    recorder = module.GatewayRecoveryObservationRecorder(
        owner, lambda after, limit: (captures.append(threading.get_ident()), page(after))[1], settings(tmp_path))
    try:
        recorder.start()
        assert entered.wait(2)
        assert owner.submit_service_call(lambda: 'available').result(timeout_seconds=1) == 'available'
        release.set()
        wait_for(lambda: len(writes) >= 2 and recorder.status()['pending_pages'] == 0)
        assert writes[0][1] is writes[1][1]
        assert captures[0] == harness.threads[0][1] != writes[0][0]
        journal = RecoveryObservationJournal(tmp_path / 'recovery.sqlite', scope_ref='account',
                    max_events=20, max_bytes=100000, timeout_seconds=0.1)
        assert journal.checkpoint('source', 'a' * 32) == 1
    finally:
        release.set()
        assert recorder.close()
        assert owner.close(2)


def test_running_owner_timeout_and_unconfirmed_close_keep_original_handle(tmp_path):
    owner, _ = owner_for(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def capture(after, limit):
        calls.append(after)
        entered.set()
        assert release.wait(3)
        return page(after)
    recorder = module.GatewayRecoveryObservationRecorder(owner, capture, settings(tmp_path))
    try:
        recorder.start()
        assert entered.wait(2)
        wait_for(lambda: recorder.status()['last_failure_code'] == 'OWNER_CALL_TIMEOUT')
        owner._fence_dispatch_shutdown()
        assert not recorder.close()
        assert recorder.status()['pending_calls'] == 1
        assert calls == [0]
        release.set()
        wait_for(lambda: recorder.status()['state'] == 'closed')
        assert recorder.close()
        assert calls == [0]
        journal = RecoveryObservationJournal(tmp_path / 'recovery.sqlite', scope_ref='account',
                    max_events=20, max_bytes=100000, timeout_seconds=.1)
        rows = journal.read_after(0, limit=10).records
        assert [row.kind for row in rows] == ['epoch', 'observation', 'capture_stopped']
        assert rows[-1].through_sequence == 1 and rows[-1].continuity == 'unknown'
    finally:
        release.set()
        assert recorder.close()
        assert owner.close(2)


def test_full_journal_retains_page_and_close_retries_same_worker(tmp_path, monkeypatch):
    owner, _ = owner_for(tmp_path)
    journals = []
    class Journal(RecoveryObservationJournal):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            journals.append(self)
    monkeypatch.setattr(module, 'RecoveryObservationJournal', Journal)
    recorder = module.GatewayRecoveryObservationRecorder(owner, lambda after, limit: page(after),
                                                            settings(tmp_path, max_events=1))
    try:
        recorder.start()
        wait_for(lambda: recorder.status()['last_failure_code'] == 'JOURNAL_WRITE_FAILED')
        assert recorder.status()['pending_pages'] == 1
        thread = recorder._thread
        assert not recorder.close()
        assert recorder._thread is thread and thread.is_alive()
        journals[0]._max_events = 20  # Test-only capacity restoration to release the retained worker.
        wait_for(lambda: recorder.status()['state'] == 'closed')
        assert recorder.close()
    finally:
        if journals:
            journals[0]._max_events = 20
        assert recorder.close()
        assert owner.close(2)


def test_existing_same_epoch_checkpoint_advances_after_validated_replay(tmp_path):
    owner, _ = owner_for(tmp_path)
    epoch = 'a' * 32
    def item(seq):
        return RecoveryCoverageObservation(epoch, os.getpid(), seq, None, 'UNKNOWN', 'CLOSED',
                                           seq, None, None, None, None, ())
    journal = RecoveryObservationJournal(tmp_path / 'recovery.sqlite', scope_ref='account',
                  max_events=20, max_bytes=100000, timeout_seconds=.1)
    journal.append_page('source', RecoveryObservationPage(epoch, os.getpid(), 0, 1, 2,
                                                            (item(1), item(2)), False))
    cursors = []
    def capture(after, limit):
        cursors.append(after)
        return RecoveryObservationPage(epoch, os.getpid(), after, 1, 2,
                    (item(1),) if after == 0 else (), False)
    recorder = module.GatewayRecoveryObservationRecorder(owner, capture, settings(tmp_path, page_limit=1))
    try:
        recorder.start()
        wait_for(lambda: 2 in cursors)
        assert cursors[0:2] == [0, 2]
    finally:
        assert recorder.close()
        assert owner.close(2)


def test_wait_exception_keeps_handle_and_claim_rejects_second_recorder(tmp_path):
    owner, _ = owner_for(tmp_path)
    class Proxy:
        def claim_service_port(self, *args):
            return owner.claim_service_port(*args)
        def submit_service_call(self, callback):
            handle = owner.submit_service_call(callback)
            class Handle:
                first = True
                def result(self, **kwargs):
                    if self.first:
                        self.first = False
                        raise RuntimeError('private details')
                    return handle.result(**kwargs)
                def cancel(self):
                    return handle.cancel()
            return Handle()
    proxy = Proxy()
    recorder = module.GatewayRecoveryObservationRecorder(proxy, lambda after, limit: page(after), settings(tmp_path))
    try:
        recorder.start()
        wait_for(lambda: recorder.status()['state'] == 'ready')
        other = module.GatewayRecoveryObservationRecorder(proxy, lambda after, limit: page(after), settings(tmp_path))
        with pytest.raises(Exception, match='SERVICE_PORT_ALREADY_CLAIMED'):
            other.start()
    finally:
        assert recorder.close()
        assert owner.close(2)


def test_unexpected_worker_exit_never_reports_pending_page_closed(tmp_path, monkeypatch):
    owner, _ = owner_for(tmp_path)
    class Journal(RecoveryObservationJournal):
        def append_page(self, *args):
            raise SystemExit('private detail')
    monkeypatch.setattr(module, 'RecoveryObservationJournal', Journal)
    recorder = module.GatewayRecoveryObservationRecorder(owner, lambda after, limit: page(after), settings(tmp_path))
    try:
        recorder.start()
        wait_for(lambda: recorder.status()['state'] == 'failed')
        assert recorder.status()['last_failure_code'] == 'RECORDER_INTERNAL_FAILURE'
        assert recorder.status()['pending_pages'] == 1
        assert not recorder.close()
    finally:
        assert owner.close(2)


def test_fence_discards_only_confirmed_queued_capture(tmp_path):
    owner, _ = owner_for(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def block_owner():
        entered.set()
        assert release.wait(3)
    running = owner.submit_service_call(block_owner)
    assert entered.wait(2)
    calls = []
    recorder = module.GatewayRecoveryObservationRecorder(
        owner, lambda after, limit: calls.append(after), settings(tmp_path))
    try:
        recorder.start()
        wait_for(lambda: recorder.status()['pending_calls'] == 1)
        owner._fence_dispatch_shutdown()
        assert recorder.close()
        assert calls == []
        assert recorder.status()['pending_stop_marker'] == 0
        journal = RecoveryObservationJournal(tmp_path / 'recovery.sqlite', scope_ref='account',
                    max_events=20, max_bytes=100000, timeout_seconds=.1)
        assert journal.read_after(0, limit=10).records == ()
    finally:
        release.set()
        running.result(timeout_seconds=2)
        assert recorder.close()
        assert owner.close(2)


def test_capture_stop_capacity_retains_worker_and_retries_same_marker(tmp_path, monkeypatch):
    owner, _ = owner_for(tmp_path)
    journals, writes = [], []
    class Journal(RecoveryObservationJournal):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            journals.append(self)
        def append_capture_stopped(self, *args, **kwargs):
            writes.append((args, kwargs))
            return super().append_capture_stopped(*args, **kwargs)
    monkeypatch.setattr(module, 'RecoveryObservationJournal', Journal)
    recorder = module.GatewayRecoveryObservationRecorder(
        owner, lambda after, limit: page(after), settings(tmp_path, max_events=2))
    try:
        recorder.start()
        wait_for(lambda: recorder.status()['state'] == 'ready')
        owner._fence_dispatch_shutdown()
        assert not recorder.close()
        wait_for(lambda: recorder.status()['last_failure_code'] == 'STOP_MARKER_WRITE_FAILED')
        assert recorder.status()['pending_stop_marker'] == 1
        assert recorder._thread.is_alive() and recorder._cursor == 1
        assert writes and all(call == writes[0] for call in writes)
        journals[0]._max_events = 3  # Test-only capacity restoration; no record is removed.
        wait_for(lambda: recorder.status()['state'] == 'closed')
        assert recorder.close()
        rows = journals[0].read_after(0, limit=10).records
        assert [row.kind for row in rows] == ['epoch', 'observation', 'capture_stopped']
        assert rows[-1].through_sequence == 1 and rows[-1].continuity == 'unknown'
    finally:
        if journals:
            journals[0]._max_events = 3
        assert recorder.close()
        assert owner.close(2)
