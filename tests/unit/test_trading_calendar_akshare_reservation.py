"""Observation reservation checks use only fake collectors and temporary roots."""

import contextlib
import os
import subprocess
import sys
import threading
import time
from datetime import date

import pytest

from bt_api_py import trading_calendar_akshare_observation as obs
from bt_api_py import trading_calendar_mock_akshare as mock
from bt_api_py import trading_calendar_observation_run as runs

RID, BID, RUN = "a" * 32, "b" * 32, "c" * 32
OIDS = ("d" * 32, "e" * 32)
SCOPES = ("1" * 64, "2" * 64)
DAY = date(2026, 11, 13)
FS = obs.TRUSTED_LOCAL_POSIX


def requests():
    return [
        {
            "observation_id": oid,
            "scope_sha256": scope,
            "venue": venue,
            "contract": contract,
            "requested_date": DAY,
            "timeout": 5,
        }
        for oid, scope, venue, contract in zip(
            OIDS, SCOPES, ("SHFE", "DCE"), ("RB2701", "I2701"), strict=True
        )
    ]


def reserve(root, **changes):
    args = dict(
        root=str(root),
        reservation_id=RID,
        batch_id=BID,
        run_id=RUN,
        requests=requests(),
        filesystem=FS,
    )
    args.update(changes)
    return obs.reserve_observations(**args)


def show(root):
    return obs.show_reservation(root=str(root), reservation_id=RID, filesystem=FS)


def attempt(root, calls, *, index=0, reservation=None, timeout=1, **changes):
    row = requests()[index]

    def collector(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("fake uncertain result")

    args = dict(
        root=str(root),
        observation_id=row["observation_id"],
        run_id=RUN,
        venue=row["venue"],
        contract=row["contract"],
        requested_date=DAY,
        timeout=timeout,
        filesystem=FS,
        reservation=reservation,
        _collector=collector,
    )
    args.update(changes)
    return obs.observe(**args)


def binding(index=0):
    return {"reservation_id": RID, "batch_id": BID, "scope_sha256": SCOPES[index]}


def test_reservation_binds_scope_request_and_cannot_be_stolen_or_replayed(tmp_path):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    assert show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"
    calls = []
    assert attempt(tmp_path, calls)["status"] == "UNKNOWN"
    assert attempt(tmp_path, calls, reservation=binding(1))["status"] == "UNKNOWN"
    assert attempt(tmp_path, calls, reservation=binding(), timeout=6)["status"] == "UNKNOWN"
    assert attempt(tmp_path, calls, reservation=binding(), contract="RB2702")["status"] == "UNKNOWN"
    assert not calls
    assert attempt(tmp_path, calls, reservation=binding())["status"] == "ATTEMPT_UNKNOWN"
    assert attempt(tmp_path, calls, reservation=binding())["status"] == "NOT_COMMITTED"
    assert attempt(tmp_path, calls, index=1, reservation=binding(1))["status"] == "ATTEMPT_UNKNOWN"
    assert len(calls) == 2


def test_legacy_id_and_overlapping_reservation_reject_whole_group(tmp_path):
    calls = []
    assert attempt(tmp_path, calls, index=1)["status"] == "ATTEMPT_UNKNOWN"
    assert reserve(tmp_path)["reason"] == "ID_ALREADY_USED"
    assert show(tmp_path)["status"] == "NOT_OBSERVED"
    assert attempt(tmp_path, calls, index=0)["status"] == "ATTEMPT_UNKNOWN"
    assert len(calls) == 2


def test_competing_groups_overlap_without_partial_allocation(tmp_path):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    other = requests()
    other[0]["observation_id"] = "f" * 32
    assert (
        reserve(
            tmp_path,
            reservation_id="3" * 32,
            batch_id="4" * 32,
            run_id="5" * 32,
            requests=other,
        )["reason"]
        == "ID_ALREADY_USED"
    )
    calls = []
    assert attempt(tmp_path, calls, observation_id="f" * 32)["status"] == "ATTEMPT_UNKNOWN"
    assert len(calls) == 1


@pytest.mark.parametrize("stage", ["insert", "commit"])
def test_transaction_interruption_never_creates_partial_group(tmp_path, monkeypatch, stage):
    original = obs._registry

    class Proxy:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            result = self.db.execute(sql, *args)
            if stage == "commit" and sql == "COMMIT":
                raise RuntimeError("uncertain commit")
            return result

        def executemany(self, sql, args):
            if stage == "insert":
                self.db.execute(sql, args[0])
                raise RuntimeError("interrupted insertion")
            return self.db.executemany(sql, args)

    @contextlib.contextmanager
    def fault(*args):
        with original(*args) as db:
            yield Proxy(db)

    monkeypatch.setattr(obs, "_registry", fault)
    assert reserve(tmp_path)["status"] == "UNKNOWN"
    monkeypatch.setattr(obs, "_registry", original)
    assert show(tmp_path)["status"] == "ATTEMPT_UNKNOWN"
    assert reserve(tmp_path)["status"] == "UNKNOWN"
    calls = []
    assert attempt(tmp_path, calls)["status"] == "UNKNOWN"
    assert attempt(tmp_path, calls, observation_id="f" * 32)["status"] == "UNKNOWN"
    assert not calls
    wrong = obs.reconcile_reservation(root=str(tmp_path), reservation_id="f" * 32, filesystem=FS)
    assert wrong["status"] == "UNKNOWN"
    assert attempt(tmp_path, calls)["status"] == "UNKNOWN"
    reconciled = obs.reconcile_reservation(root=str(tmp_path), reservation_id=RID, filesystem=FS)
    if stage == "commit":
        assert reconciled["status"] == "VISIBLE_UNAUTHENTICATED"
        assert show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"
        assert attempt(tmp_path, calls)["status"] == "UNKNOWN"
        assert not calls
    else:
        assert reconciled["status"] == "UNKNOWN"
        assert attempt(tmp_path, calls)["status"] == "UNKNOWN"
        assert not calls


def test_uncertain_attempt_marker_keeps_original_id_blocked(tmp_path, monkeypatch):
    original = runs._write_once

    def uncertain(*args):
        result = original(*args)
        if args[2].startswith("reservation-attempt-") and result[0] == "COMMITTED":
            return "COMMIT_UNKNOWN", "INJECTED"
        return result

    monkeypatch.setattr(runs, "_write_once", uncertain)
    assert reserve(tmp_path)["status"] == "UNKNOWN"
    monkeypatch.setattr(runs, "_write_once", original)
    assert show(tmp_path)["status"] == "ATTEMPT_UNKNOWN"
    assert reserve(tmp_path)["status"] == "UNKNOWN"


def test_competing_consumers_call_collector_once(tmp_path):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    entered, release = threading.Event(), threading.Event()
    calls, results = [], []

    def collector(**kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        raise RuntimeError("uncertain")

    def consume():
        results.append(
            obs.observe(
                root=str(tmp_path),
                observation_id=OIDS[0],
                run_id=RUN,
                venue="SHFE",
                contract="RB2701",
                requested_date=DAY,
                timeout=1,
                filesystem=FS,
                reservation=binding(),
                _collector=collector,
            )
        )

    worker = threading.Thread(target=consume)
    worker.start()
    try:
        assert entered.wait(5)
        consume()
    finally:
        release.set()
        worker.join(5)
    assert len(calls) == 1
    assert {result["status"] for result in results} == {
        "ATTEMPT_UNKNOWN",
        "NOT_COMMITTED",
    }


def test_concurrent_overlapping_reservations_never_both_commit(tmp_path):
    start = threading.Barrier(3)
    results = []

    def worker(reservation_id, batch_id, run_id):
        start.wait()
        results.append(
            reserve(
                tmp_path,
                reservation_id=reservation_id,
                batch_id=batch_id,
                run_id=run_id,
            )
        )

    threads = [
        threading.Thread(target=worker, args=ids)
        for ids in [(RID, BID, RUN), ("3" * 32, "4" * 32, "5" * 32)]
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(5)
    assert len(results) == 2
    assert sum(result["status"] == "COMMITTED" for result in results) == 1
    calls = []
    assert attempt(tmp_path, calls)["status"] == "UNKNOWN"
    assert not calls


def test_invalid_group_never_creates_storage(tmp_path):
    group = requests()
    group[1]["observation_id"] = group[0]["observation_id"]
    assert reserve(tmp_path, requests=group)["status"] == "NOT_COMMITTED"
    assert not (tmp_path / "akshare-observations").exists()


def test_missing_database_or_unsafe_journal_fails_closed(tmp_path):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    folder = tmp_path / "akshare-observations"
    journal = folder / (obs._REGISTRY + "-journal")
    journal.write_text("unexpected")
    journal.chmod(0o600)
    calls = []
    assert attempt(tmp_path, calls, reservation=binding())["status"] == "UNKNOWN"
    journal.unlink()
    (folder / obs._REGISTRY).unlink()
    assert show(tmp_path)["status"] == "UNKNOWN"
    assert attempt(tmp_path, calls)["status"] == "UNKNOWN"
    assert not calls


def test_missing_registry_and_marker_with_attempt_never_reinitializes(tmp_path):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    folder = tmp_path / "akshare-observations"
    (folder / obs._REGISTRY).unlink()
    (folder / obs._REGISTRY_MARKER).unlink()
    calls = []
    assert attempt(tmp_path, calls)["status"] == "UNKNOWN"
    assert not calls
    assert not (folder / obs._REGISTRY).exists()
    assert not (folder / obs._REGISTRY_MARKER).exists()


def test_mock_unauthorized_cannot_consume_reserved_descriptor(tmp_path, monkeypatch):
    fds, _ = runs._open_runs(str(tmp_path), create=True)
    runs._close(fds)
    assert reserve(tmp_path)["status"] == "COMMITTED"
    monkeypatch.setattr(mock, "_guard", lambda *args: None)
    monkeypatch.setattr(mock, "_body", lambda **kwargs: {"fallback": {}})
    calls = []
    original = obs.observe

    def collector(**kwargs):
        calls.append(1)
        raise RuntimeError("fake collector")

    monkeypatch.setattr(obs, "observe", lambda **kwargs: original(**kwargs, _collector=collector))
    kwargs = dict(
        root=str(tmp_path),
        run_id=RUN,
        observation_id=OIDS[0],
        year=2026,
        request={
            "venue": "SHFE",
            "contract": "RB2701",
            "requested_date": DAY,
            "kind": "future",
            "timeout": 5,
        },
        filesystem=FS,
    )
    descriptor = tmp_path / "observation-runs" / f"mock-akshare-{OIDS[0]}.json"
    assert mock.observe_once(**kwargs)["descriptor_state"] == "NOT_ATTEMPTED"
    assert not descriptor.exists() and not calls
    result = mock.observe_once(**kwargs, reservation=binding())
    assert result["descriptor_state"] == "COMMITTED"
    assert result["observation_state"] == "ATTEMPT_UNKNOWN"
    assert descriptor.exists() and calls == [1]


def test_unknown_descriptor_does_not_start_collector(tmp_path, monkeypatch):
    fds, _ = runs._open_runs(str(tmp_path), create=True)
    runs._close(fds)
    monkeypatch.setattr(mock, "_guard", lambda *args: None)
    monkeypatch.setattr(mock, "_body", lambda **kwargs: {"fallback": {}})
    original_write = runs._write_once

    def uncertain(*args):
        result = original_write(*args)
        if args[2].startswith("mock-akshare-"):
            return "COMMIT_UNKNOWN", "INJECTED"
        return result

    monkeypatch.setattr(runs, "_write_once", uncertain)
    calls = []
    original = obs.observe

    def collector(**kwargs):
        calls.append(1)
        raise RuntimeError("must not run")

    monkeypatch.setattr(obs, "observe", lambda **kwargs: original(**kwargs, _collector=collector))
    result = mock.observe_once(
        root=str(tmp_path),
        run_id=RUN,
        observation_id=OIDS[0],
        year=2026,
        request={
            "venue": "SHFE",
            "contract": "RB2701",
            "requested_date": DAY,
            "kind": "future",
            "timeout": 5,
        },
        filesystem=FS,
    )
    assert result["descriptor_state"] == "COMMIT_UNKNOWN"
    assert result["observation_state"] == "NOT_ATTEMPTED"
    assert not calls


def test_exception_after_descriptor_does_not_claim_not_committed(tmp_path, monkeypatch):
    fds, _ = runs._open_runs(str(tmp_path), create=True)
    runs._close(fds)
    descriptor = obs._MockDescriptor(str(tmp_path), OIDS[0], b"descriptor", None)
    original = obs._MockDescriptor.install

    def fail_after_install(value):
        original(value)
        raise OSError("uncertain continuation")

    monkeypatch.setattr(obs._MockDescriptor, "install", fail_after_install)
    calls = []

    def collector(**kwargs):
        calls.append(1)
        raise AssertionError("must not run")

    result = obs.observe(
        root=str(tmp_path),
        observation_id=OIDS[0],
        run_id=RUN,
        venue="SHFE",
        contract="RB2701",
        requested_date=DAY,
        timeout=1,
        filesystem=FS,
        _mock_descriptor=descriptor,
        _collector=collector,
    )
    assert result["status"] == "UNKNOWN"
    assert result["descriptor_state"] == "COMMITTED"
    assert not calls


def test_descriptor_is_installed_while_other_process_cannot_claim(tmp_path, monkeypatch):
    fds, _ = runs._open_runs(str(tmp_path), create=True)
    runs._close(fds)
    assert reserve(tmp_path)["status"] == "COMMITTED"
    monkeypatch.setattr(mock, "_guard", lambda *args: None)
    monkeypatch.setattr(mock, "_body", lambda **kwargs: {"fallback": {}})
    entered, release = threading.Event(), threading.Event()
    original_install = obs._MockDescriptor.install

    def block_after_install(descriptor):
        original_install(descriptor)
        entered.set()
        assert release.wait(8)

    monkeypatch.setattr(obs._MockDescriptor, "install", block_after_install)
    calls, result = [], []
    original_observe = obs.observe

    def collector(**kwargs):
        calls.append(1)
        raise RuntimeError("fake uncertain collector")

    monkeypatch.setattr(
        obs, "observe", lambda **kwargs: original_observe(**kwargs, _collector=collector)
    )

    def run_mock():
        result.append(
            mock.observe_once(
                root=str(tmp_path),
                run_id=RUN,
                observation_id=OIDS[0],
                year=2026,
                request={
                    "venue": "SHFE",
                    "contract": "RB2701",
                    "requested_date": DAY,
                    "kind": "future",
                    "timeout": 5,
                },
                filesystem=FS,
                reservation=binding(),
            )
        )

    thread = threading.Thread(target=run_mock)
    thread.start()
    try:
        assert entered.wait(8)
        script = """
import sys
from datetime import date
from bt_api_py import trading_calendar_akshare_observation as o
def collect(**kwargs):
    open(sys.argv[2], "w").write("called")
    raise RuntimeError()
r=o.observe(root=sys.argv[1], observation_id="f"*32, run_id="c"*32,
            venue="SHFE", contract="RB2701", requested_date=date(2026,11,13), timeout=1,
            filesystem=o.TRUSTED_LOCAL_POSIX, _collector=collect)
print(r["status"])
"""
        child = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), str(tmp_path / "called")],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        assert child.stdout.strip() == "UNKNOWN"
        assert not (tmp_path / "called").exists()
    finally:
        release.set()
        thread.join(8)
    assert result[0]["descriptor_state"] == "COMMITTED"
    assert calls == [1]


def test_pending_blocks_other_process_after_transaction_rollback(tmp_path):
    script = r"""
import contextlib, pathlib, sys, time
from datetime import date
from bt_api_py import trading_calendar_akshare_observation as o
root = pathlib.Path(sys.argv[1])
original = o._registry
class Proxy:
    def __init__(self, db): self.db = db
    def execute(self, *args): return self.db.execute(*args)
    def executemany(self, sql, args):
        self.db.execute(sql, args[0])
        (root / "inside").touch()
        deadline = time.monotonic() + 8
        while not (root / "release").exists() and time.monotonic() < deadline: time.sleep(0.01)
        raise RuntimeError("interrupted")
@contextlib.contextmanager
def fault(*args):
    with original(*args) as db: yield Proxy(db)
o._registry = fault
rows = [{"observation_id": oid * 32, "scope_sha256": scope * 64, "venue": venue,
         "contract": contract, "requested_date": date(2026,11,13), "timeout": 5}
        for oid, scope, venue, contract in [("d","1","SHFE","RB2701"),("e","2","DCE","I2701")]]
print(o.reserve_observations(root=str(root),reservation_id="a"*32,batch_id="b"*32,
      run_id="c"*32,requests=rows,filesystem=o.TRUSTED_LOCAL_POSIX)["status"])
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    calls = []
    try:
        deadline = time.monotonic() + 8
        while not (tmp_path / "inside").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "inside").exists()
        assert attempt(tmp_path, calls)["status"] == "UNKNOWN"
        (tmp_path / "release").touch()
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == 0 and stdout.strip() == "UNKNOWN", stderr
        assert attempt(tmp_path, calls)["status"] == "UNKNOWN"
        assert attempt(tmp_path, calls, observation_id="f" * 32)["status"] == "UNKNOWN"
        assert not calls
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize("fault", ["link", "mode", "corrupt"])
def test_unsafe_registry_fails_closed(tmp_path, fault):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    database = tmp_path / "akshare-observations" / obs._REGISTRY
    if fault == "link":
        os.link(database, tmp_path / "extra-link")
    elif fault == "mode":
        database.chmod(0o644)
    else:
        database.write_bytes(b"broken")
    calls = []
    assert attempt(tmp_path, calls, reservation=binding())["status"] == "UNKNOWN"
    assert show(tmp_path)["status"] == "UNKNOWN"
    assert not calls


def test_mock_forwarding_preserves_binding_and_deadline_timeout(tmp_path, monkeypatch):
    fds, _ = runs._open_runs(str(tmp_path), create=True)
    runs._close(fds)
    monkeypatch.setattr(mock, "_guard", lambda *args: None)
    monkeypatch.setattr(mock, "_body", lambda **kwargs: {})
    monkeypatch.setattr(mock.time, "monotonic", lambda: 100.0)
    captured = []

    def observe(**kwargs):
        captured.append(kwargs)
        return {"record_state": "ATTEMPT_UNKNOWN", "observation_status": "UNKNOWN"}

    monkeypatch.setattr(mock.observation, "observe", observe)
    mock.observe_once(
        root=str(tmp_path),
        run_id=RUN,
        observation_id=OIDS[0],
        year=2026,
        request={
            "venue": "SHFE",
            "contract": "RB2701",
            "requested_date": DAY,
            "kind": "future",
            "timeout": 5,
        },
        filesystem=FS,
        deadline_monotonic=102,
        reservation=binding(),
    )
    assert captured[0]["reservation"] == binding()
    assert captured[0]["timeout"] == 2
