"""Offline protected-read tests; no observation lock or SQLite writes by reader."""

import hashlib
import os
import sqlite3
import threading

import pytest

from bt_api_py import trading_calendar_akshare_observation as obs
from tests.unit.test_trading_calendar_akshare_reservation import (
    BID,
    FS,
    RID,
    RUN,
    requests,
    reserve,
)


def verify(root, **changes):
    rows = requests()
    payload = obs._reservation_payload(RID, BID, RUN, rows)
    digest = obs.storage._unwrap(obs.storage._envelope(payload))[1]
    args = dict(
        root=str(root),
        reservation_id=RID,
        batch_id=BID,
        run_id=RUN,
        requests=rows,
        payload_sha256=digest,
        filesystem=FS,
    )
    args.update(changes)
    return obs.verify_reservation_readonly(**args)


def snapshot(folder):
    directory = folder.stat()
    result = {}
    for path in folder.iterdir():
        info = path.stat()
        result[path.name] = (
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return directory.st_mtime_ns, directory.st_ctime_ns, result


def test_readonly_check_does_not_take_observation_lock_or_write(tmp_path, monkeypatch):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    folder = tmp_path / "akshare-observations"
    before = snapshot(folder)
    monkeypatch.setattr(obs.storage, "_lock", lambda *args: pytest.fail("observation lock"))
    result = verify(tmp_path)
    assert result["status"] == "VISIBLE_UNAUTHENTICATED"
    assert result["source_authentication"] == "NOT_PROVEN"
    assert snapshot(folder) == before


@pytest.mark.parametrize(
    "fault", ["reservation", "batch", "run", "scope", "timeout", "digest", "oid"]
)
def test_readonly_check_rejects_identity_or_request_mix(tmp_path, fault):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    changes = {}
    if fault == "reservation":
        changes["reservation_id"] = "f" * 32
    if fault == "batch":
        changes["batch_id"] = "f" * 32
    if fault == "run":
        changes["run_id"] = "f" * 32
    if fault == "digest":
        changes["payload_sha256"] = "f" * 64
    if fault in {"scope", "timeout", "oid"}:
        rows = requests()
        if fault == "scope":
            rows[0]["scope_sha256"] = "f" * 64
        if fault == "timeout":
            rows[0]["timeout"] = 4
        if fault == "oid":
            rows[0]["observation_id"] = "f" * 32
        changes["requests"] = rows
    if fault != "digest":
        payload = obs._reservation_payload(
            changes.get("reservation_id", RID),
            changes.get("batch_id", BID),
            changes.get("run_id", RUN),
            changes.get("requests", requests()),
        )
        changes["payload_sha256"] = obs.storage._unwrap(obs.storage._envelope(payload))[1]
    assert verify(tmp_path, **changes)["status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "empty",
        "corrupt",
        "pending",
        "journal",
        "wal",
        "shm",
        "hardlink",
        "marker",
        "attempt",
    ],
)
def test_readonly_check_rejects_missing_or_uncertain_registry(tmp_path, fault):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    folder = tmp_path / "akshare-observations"
    path = folder / obs._REGISTRY
    if fault in {"missing", "empty"}:
        path.unlink()
        if fault == "empty":
            db = sqlite3.connect(path)
            db.close()
            path.chmod(0o600)
    elif fault == "corrupt":
        path.write_bytes(b"invalid")
    elif fault == "hardlink":
        os.link(path, tmp_path / "hardlink")
    elif fault == "marker":
        (folder / obs._REGISTRY_MARKER).unlink()
    elif fault == "attempt":
        (folder / f"reservation-attempt-{RID}.json").unlink()
    else:
        name = obs._RESERVATION_PENDING if fault == "pending" else obs._REGISTRY + "-" + fault
        other = folder / name
        other.write_bytes(b"uncertain")
        other.chmod(0o600)
    assert verify(tmp_path)["status"] == "UNKNOWN"


def test_readonly_check_rejects_sqlite_writer_during_read_transaction(tmp_path, monkeypatch):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    real_connect = sqlite3.connect
    reading, release = threading.Event(), threading.Event()

    class Proxy:
        def __init__(self, db):
            self.db = db

        def setlimit(self, *args):
            return self.db.setlimit(*args)

        def execute(self, sql, *args):
            cursor = self.db.execute(sql, *args)
            if sql.startswith("SELECT batch_id,run_id,payload"):
                reading.set()
                assert release.wait(8)
            return cursor

        def close(self):
            return self.db.close()

    monkeypatch.setattr(
        obs.sqlite3, "connect", lambda *args, **kwargs: Proxy(real_connect(*args, **kwargs))
    )
    results = []
    thread = threading.Thread(target=lambda: results.append(verify(tmp_path)))
    thread.start()
    writer = None
    try:
        assert reading.wait(8)
        writer = real_connect(
            tmp_path / "akshare-observations" / obs._REGISTRY, timeout=0, isolation_level=None
        )
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO reservations VALUES(?,?,?,?)", ("1" * 32, "2" * 32, "3" * 32, b"test")
        )
        release.set()
        thread.join(8)
        assert results[0]["status"] == "UNKNOWN"
    finally:
        release.set()
        thread.join(8)
        if writer is not None:
            writer.execute("ROLLBACK")
            writer.close()


def test_readonly_check_rejects_commit_after_snapshot_closes(tmp_path, monkeypatch):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    real_connect = sqlite3.connect

    class Proxy:
        def __init__(self, db):
            self.db = db

        def setlimit(self, *args):
            return self.db.setlimit(*args)

        def execute(self, sql, *args):
            cursor = self.db.execute(sql, *args)
            if sql == "ROLLBACK":
                writer = real_connect(
                    tmp_path / "akshare-observations" / obs._REGISTRY,
                    timeout=0,
                    isolation_level=None,
                )
                try:
                    writer.execute("BEGIN IMMEDIATE")
                    writer.execute(
                        "INSERT INTO reservations VALUES(?,?,?,?)",
                        ("1" * 32, "2" * 32, "3" * 32, b"test"),
                    )
                    writer.execute("COMMIT")
                finally:
                    writer.close()
            return cursor

        def close(self):
            return self.db.close()

    monkeypatch.setattr(
        obs.sqlite3, "connect", lambda *args, **kwargs: Proxy(real_connect(*args, **kwargs))
    )
    assert verify(tmp_path)["status"] == "UNKNOWN"
