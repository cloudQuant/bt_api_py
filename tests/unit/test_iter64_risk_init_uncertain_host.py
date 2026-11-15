"""Synthetic storage-init uncertainty; no Gateway or account operations."""

import gc
import json
import logging
import multiprocessing
import os
import signal
import sqlite3
import threading
import time
import weakref
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from bt_api_py.risk_service_config import load_risk_service_config
from bt_api_py.risk_service_host import _RETAINED_HOSTS, RiskServiceHost, RiskServiceStartError
from tests.unit.test_iter64_risk_service_host import setup


def _contend(path, results):
    config = load_risk_service_config(path)
    host = RiskServiceHost(config)
    try:
        host.open_storage()
    except RiskServiceStartError as error:
        results.put(error.code)
    else:
        results.put("opened")
    finally:
        host.close()


def _status_with_uncertain_init(path, phase, results):
    import bt_api_risk.pretrade as pretrade

    import bt_api_py.risk_service_host as module

    logging.disable(logging.CRITICAL)
    ledger_type = pretrade.PretradeLedger
    original_print = module._print

    def capture(value):
        original_print(value)
        results.put(value)

    def injected_ledger(journal, fence):
        original = journal._transaction

        @contextmanager
        def injected_transaction():
            with original() as connection:
                yield connection
                if phase == "precommit":
                    raise RuntimeError("synthetic precommit failure")
            if phase == "postcommit":
                raise RuntimeError("synthetic postcommit failure")

        journal._transaction = injected_transaction
        return ledger_type(journal, fence)

    pretrade.PretradeLedger = injected_ledger
    module._print = capture
    module.main(["status", "--config", path])


@pytest.mark.parametrize("phase", ["precommit", "postcommit"])
def test_uncertain_initialization_retains_original_fence(tmp_path, monkeypatch, phase):
    import bt_api_risk.pretrade as pretrade

    path = setup(tmp_path)
    config = load_risk_service_config(path)
    host = RiskServiceHost(config)
    ledger_type = pretrade.PretradeLedger
    gateway_calls = []
    monkeypatch.setattr(host, "start_gateway", lambda: gateway_calls.append(True))

    def injected_ledger(journal, fence):
        original = journal._transaction

        @contextmanager
        def injected_transaction():
            with original() as connection:
                yield connection
                if phase == "precommit":
                    raise RuntimeError("synthetic precommit failure")
            if phase == "postcommit":
                raise RuntimeError("synthetic postcommit failure")

        with monkeypatch.context() as patch:
            patch.setattr(journal, "_transaction", injected_transaction)
            return ledger_type(journal, fence)

    monkeypatch.setattr(pretrade, "PretradeLedger", injected_ledger)
    try:
        with pytest.raises(RiskServiceStartError) as error:
            host.start()
        assert error.value.code == "RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN"
        assert not gateway_calls
        assert host in _RETAINED_HOSTS
        journal, fence = host._uncertain_storage
        assert fence.owner_id == journal._owner_id
        with sqlite3.connect(config.risk_database) as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='risk_pretrade_meta'"
            ).fetchone()
            assert bool(exists) is (phase == "postcommit")
        assert host.close() is False
        with pytest.raises(RiskServiceStartError, match="RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN"):
            host.start()
        assert not gateway_calls

        results = []
        thread = threading.Thread(target=lambda: results.append(host.close()))
        thread.start()
        thread.join(5)
        assert not thread.is_alive() and results == [False]

        # Same-thread and another-thread contenders cannot obtain the original fence.
        contender = RiskServiceHost(config)
        with pytest.raises(RiskServiceStartError, match="RISK_SERVICE_FENCE_HELD"):
            contender.open_storage()
        assert contender.close()
        thread_result = []
        thread = threading.Thread(target=_contend, args=(str(path), _ListQueue(thread_result)))
        thread.start()
        thread.join(5)
        assert not thread.is_alive() and thread_result == ["RISK_SERVICE_FENCE_HELD"]

        # An independent process checks the OS-level lock while the owner lives.
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        process = context.Process(target=_contend, args=(str(path), queue))
        process.start()
        process.join(15)
        assert process.exitcode == 0
        assert queue.get(timeout=2) == "RISK_SERVICE_FENCE_HELD"
    finally:
        # Only this test's synthetic owner is released, on its original thread.
        host._stack.close()
        _RETAINED_HOSTS.discard(host)


class _ListQueue:
    def __init__(self, values):
        self.values = values

    def put(self, value):
        self.values.append(value)


@pytest.mark.parametrize("journal_error", [False, True])
def test_ordinary_initialization_error_releases_fence(tmp_path, monkeypatch, journal_error):
    import bt_api_risk.pretrade as pretrade
    from bt_api_risk.risk_order_journal import RiskOrderJournalError

    config = load_risk_service_config(setup(tmp_path))
    host = RiskServiceHost(config)
    with monkeypatch.context() as patch:
        def fail(*_args):
            if journal_error:
                raise RiskOrderJournalError("PRETRADE_SCHEMA_MISMATCH")
            raise ValueError("synthetic schema error")
        patch.setattr(pretrade, "PretradeLedger", fail)
        with pytest.raises(RiskServiceStartError, match="RISK_SERVICE_STORAGE_SCHEMA_INVALID"):
            host.start()
    assert host.close()
    other = RiskServiceHost(config)
    try:
        other.open_storage()
    finally:
        assert other.close()


def test_normal_storage_close_must_run_on_original_thread(tmp_path):
    config = load_risk_service_config(setup(tmp_path))
    host = RiskServiceHost(config)
    host.open_storage()
    host._started = True
    host.pump = SimpleNamespace(verified_epoch="fixture")
    assert host.ready() is True
    results = []
    thread = threading.Thread(target=lambda: results.append(host.close()))
    thread.start()
    thread.join(5)
    try:
        assert not thread.is_alive() and results == [False]
        assert host.stop_event.is_set() and host._started is True
        assert host.ready() is False
        contender = RiskServiceHost(config)
        with pytest.raises(RiskServiceStartError, match="RISK_SERVICE_FENCE_HELD"):
            contender.open_storage()
        assert contender.close()
    finally:
        assert host.close()
    other = RiskServiceHost(config)
    try:
        other.open_storage()
    finally:
        assert other.close()


def test_cross_thread_entry_points_refuse_before_downstream_calls(tmp_path):
    host = RiskServiceHost(load_risk_service_config(setup(tmp_path)))
    host.open_storage()
    calls = []

    class FakeService:
        state = "stopped"

        def serve_once(self, *_args):
            calls.append("serve")

    class FakeLedger:
        @property
        def fence(self):
            calls.append("fence")

    host.service_host = FakeService()
    host.ledger = FakeLedger()
    results = []

    def invoke():
        for method in (host.open_storage, host.build_engine, host.start_gateway,
                       host.start_service_host, host.start, host.run_once, host.run):
            try:
                method()
            except RiskServiceStartError as error:
                results.append(error.code)
            else:
                results.append("unexpected success")
        results.append(host.ready())

    thread = threading.Thread(target=invoke)
    thread.start()
    thread.join(5)
    try:
        assert not thread.is_alive()
        assert results == ["RISK_SERVICE_HOST_THREAD_MISMATCH"] * 7 + [False]
        assert calls == []
    finally:
        assert host.close()


def test_failed_storage_cleanup_cannot_report_success_on_retry(tmp_path, caplog):
    host = RiskServiceHost(load_risk_service_config(setup(tmp_path)))
    calls = []

    def failed_callback():
        calls.append("called")
        raise RuntimeError("synthetic cleanup failure")

    host._stack.callback(failed_callback)
    try:
        assert host.close() is False
        assert host in _RETAINED_HOSTS
        assert "Risk storage cleanup failed; outcome uncertain" in caplog.text
        assert host.close() is False
        assert calls == ["called"]
        with pytest.raises(RiskServiceStartError, match="RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN"):
            host.open_storage()
    finally:
        _RETAINED_HOSTS.discard(host)


def test_cli_cleanup_log_does_not_claim_fence_is_held(monkeypatch, caplog):
    import bt_api_py.risk_service_host as module

    class Host:
        def __init__(self):
            self.outcomes = iter((False, True))

        def close(self):
            return next(self.outcomes)

    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    module._wait_for_cleanup(Host())
    assert "account fence ownership unconfirmed" in caplog.text
    assert "retaining account fence" not in caplog.text


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
@pytest.mark.parametrize("uncertain", [False, True])
def test_forked_child_api_calls_have_no_downstream_effects(tmp_path, monkeypatch, uncertain):
    import bt_api_risk.pretrade as pretrade
    from bt_api_risk.risk_order_journal import RiskOrderJournalError

    path = setup(tmp_path)
    host = RiskServiceHost(load_risk_service_config(path))
    if uncertain:
        def fail(*_args):
            raise RiskOrderJournalError("PRETRADE_INIT_COMMIT_UNCERTAIN")
        monkeypatch.setattr(pretrade, "PretradeLedger", fail)
        with pytest.raises(RiskServiceStartError, match="RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN"):
            host.open_storage()
    else:
        host.open_storage()

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        calls = []

        class FakeService:
            def serve_once(self, *_args):
                calls.append("serve")

        class FakeLedger:
            @property
            def fence(self):
                calls.append("fence")

        host.service_host = FakeService()
        host.ledger = FakeLedger()
        values = []
        for method in (host.open_storage, host.build_engine, host.start_gateway,
                       host.start_service_host, host.start, host.run_once, host.run):
            try:
                method()
            except RiskServiceStartError as error:
                values.append(error.code)
            else:
                values.append("unexpected success")
        values.extend((host.ready(), host.close(), calls))
        os.write(write_fd, json.dumps(values).encode("ascii"))
        os.close(write_fd)
        # Do not finalize inherited generators in this test: their child-side
        # finalization is a separate, currently unresolved lock-release gap.
        os._exit(0)
    os.close(write_fd)
    try:
        raw = os.read(read_fd, 4096)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        assert json.loads(raw) == ["RISK_SERVICE_HOST_PROCESS_MISMATCH"] * 7 + [False, False, []]
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        contender = context.Process(target=_contend, args=(str(path), queue))
        contender.start()
        contender.join(15)
        assert contender.exitcode == 0
        assert queue.get(timeout=2) == "RISK_SERVICE_FENCE_HELD"
    finally:
        os.close(read_fd)
        if uncertain:
            host._stack.close()
            _RETAINED_HOSTS.discard(host)
        else:
            assert host.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
@pytest.mark.parametrize("phase", ["normal", "precommit", "postcommit"])
def test_fork_child_gc_does_not_unlock_parent_fence(tmp_path, monkeypatch, phase):
    import bt_api_risk.pretrade as pretrade

    path = setup(tmp_path)
    host = RiskServiceHost(load_risk_service_config(path))
    if phase == "normal":
        host.open_storage()
    else:
        ledger_type = pretrade.PretradeLedger

        def injected_ledger(journal, fence):
            original = journal._transaction

            @contextmanager
            def injected_transaction():
                with original() as connection:
                    yield connection
                    if phase == "precommit":
                        raise RuntimeError("synthetic precommit failure")
                if phase == "postcommit":
                    raise RuntimeError("synthetic postcommit failure")

            with monkeypatch.context() as patch:
                patch.setattr(journal, "_transaction", injected_transaction)
                return ledger_type(journal, fence)

        with monkeypatch.context() as patch:
            patch.setattr(pretrade, "PretradeLedger", injected_ledger)
            with pytest.raises(RiskServiceStartError, match="RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN"):
                host.open_storage()

    # This test process must own only its synthetic host before clearing the
    # fork child's copied registry; the parent registry is unaffected by fork.
    assert {host} >= _RETAINED_HOSTS
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        ref = weakref.ref(host)
        _RETAINED_HOSTS.clear()
        del host
        gc.collect()
        os.write(write_fd, b"collected" if ref() is None else b"retained")
        os.close(write_fd)
        os._exit(0)
    os.close(write_fd)
    context = multiprocessing.get_context("spawn")

    def contend():
        queue = context.Queue()
        process = context.Process(target=_contend, args=(str(path), queue))
        process.start()
        process.join(15)
        assert process.exitcode == 0
        return queue.get(timeout=2)

    try:
        result = os.read(read_fd, 32)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        assert result == b"collected"
        assert contend() == "RISK_SERVICE_FENCE_HELD"
    finally:
        os.close(read_fd)
        # Test-only disposal of synthetic temporary storage, not a recovery API.
        if phase == "normal":
            assert host.close()
        else:
            host._stack.close()
            _RETAINED_HOSTS.discard(host)
    assert contend() == "opened"


@pytest.mark.parametrize("phase", ["precommit", "postcommit"])
def test_status_retains_uncertain_fence_until_process_exit(tmp_path, phase):
    path = setup(tmp_path)
    config = load_risk_service_config(path)
    config.risk_database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.risk_database):
        pass
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    status = context.Process(target=_status_with_uncertain_init, args=(str(path), phase, results))
    contender = None
    status.start()
    try:
        assert results.get(timeout=15) == {
            "running": None, "code": "RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN"
        }
        assert status.is_alive()
        os.kill(status.pid, signal.SIGTERM)
        time.sleep(0.2)
        assert status.is_alive()
        contender = context.Process(target=_contend, args=(str(path), results))
        contender.start()
        contender.join(15)
        assert contender.exitcode == 0
        assert results.get(timeout=2) == "RISK_SERVICE_FENCE_HELD"
        assert status.is_alive()
    finally:
        # This is solely the spawned test process with synthetic temporary storage.
        status.kill()
        status.join(15)
        if contender is not None:
            contender.join(15)
