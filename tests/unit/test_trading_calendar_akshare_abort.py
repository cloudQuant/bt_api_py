"""Offline tests of explicit original-ID reservation abort and fencing."""

import contextlib
import os
import sqlite3
import stat

import pytest

from bt_api_py import trading_calendar_akshare_observation as obs
from tests.unit.test_trading_calendar_akshare_reservation import (
    BID,
    FS,
    OIDS,
    RID,
    RUN,
    attempt,
    requests,
    reserve,
    show,
)


def pending_rollback(root, monkeypatch):
    original = obs._registry

    class Proxy:
        def __init__(self, db):
            self.db = db

        def execute(self, *args):
            return self.db.execute(*args)

        def executemany(self, sql, args):
            self.db.execute(sql, args[0])
            raise OSError("injected rollback")

    @contextlib.contextmanager
    def fault(*args):
        with original(*args) as db:
            yield Proxy(db)

    monkeypatch.setattr(obs, "_registry", fault)
    assert reserve(root)["status"] == "UNKNOWN"
    monkeypatch.setattr(obs, "_registry", original)
    assert show(root)["status"] == "ATTEMPT_UNKNOWN"


def abort(root, reservation_id=RID):
    return obs.abort_reservation(root=str(root), reservation_id=reservation_id, filesystem=FS)


def assert_sealed(root):
    calls = []
    for value in (RID, BID, RUN, *OIDS):
        result = attempt(root, calls, observation_id=value, run_id="0" * 32)
        assert result["status"] == "UNKNOWN", (value, result)
        result = attempt(root, calls, observation_id="f" * 32, run_id=value)
        assert result["status"] == "UNKNOWN", (value, result)
    assert not calls


def test_explicit_abort_seals_every_identity_and_allows_other_groups(tmp_path, monkeypatch):
    pending_rollback(tmp_path, monkeypatch)
    assert abort(tmp_path)["status"] == "ABORTED"
    assert show(tmp_path)["status"] == "ABORTED_RELEASE_UNPROVEN"
    assert abort(tmp_path)["status"] == "ABORTED_RELEASE_UNPROVEN"
    assert (
        obs.reconcile_reservation(root=str(tmp_path), reservation_id=RID, filesystem=FS)["status"]
        == "ABORTED_RELEASE_UNPROVEN"
    )
    assert_sealed(tmp_path)
    assert reserve(tmp_path)["status"] == "UNKNOWN"
    assert (
        reserve(
            tmp_path,
            reservation_id="3" * 32,
            batch_id="4" * 32,
            run_id="5" * 32,
            requests=[
                {**item, "observation_id": oid * 32}
                for item, oid in zip(requests(), ("6", "7"), strict=True)
            ],
        )["status"]
        == "COMMITTED"
    )


@pytest.mark.parametrize("role", ["reservation_id", "batch_id", "run_id", "observation_id"])
def test_aborted_ids_cannot_be_reused_in_other_roles(tmp_path, monkeypatch, role):
    pending_rollback(tmp_path, monkeypatch)
    assert abort(tmp_path)["status"] == "ABORTED"
    group = requests()
    group[0]["observation_id"] = "6" * 32
    group[1]["observation_id"] = "7" * 32
    args = {"reservation_id": "3" * 32, "batch_id": "4" * 32, "run_id": "5" * 32, "requests": group}
    if role == "observation_id":
        group[0]["observation_id"] = BID
    else:
        args[role] = OIDS[0]
    assert reserve(tmp_path, **args)["reason"] == "ID_ALREADY_USED"
    assert_sealed(tmp_path)


@pytest.mark.parametrize("role", ["reservation_id", "batch_id", "run_id", "observation_id"])
def test_normal_reservations_also_fence_cross_role_ids(tmp_path, role):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    group = requests()
    group[0]["observation_id"] = "6" * 32
    group[1]["observation_id"] = "7" * 32
    args = {"reservation_id": "3" * 32, "batch_id": "4" * 32, "run_id": "5" * 32, "requests": group}
    if role == "observation_id":
        group[0]["observation_id"] = BID
    else:
        args[role] = OIDS[0]
    assert reserve(tmp_path, **args)["reason"] == "ID_ALREADY_USED"
    calls = []
    assert attempt(tmp_path, calls, observation_id=RID, run_id="0" * 32)["status"] == "UNKNOWN"
    assert not calls


def test_abort_tombstone_and_identity_mappings_are_immutable(tmp_path, monkeypatch):
    pending_rollback(tmp_path, monkeypatch)
    assert abort(tmp_path)["status"] == "ABORTED"
    database = tmp_path / "akshare-observations" / obs._REGISTRY
    db = sqlite3.connect(database)
    try:
        for statement, args in (
            ("UPDATE reservations SET payload=? WHERE reservation_id=?", (b"changed", RID)),
            ("DELETE FROM reservations WHERE reservation_id=?", (RID,)),
            (
                "UPDATE observation_ids SET reservation_id=? WHERE observation_id=?",
                ("0" * 32, OIDS[0]),
            ),
            ("DELETE FROM observation_ids WHERE observation_id=?", (OIDS[0],)),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement, args)
            db.rollback()
    finally:
        db.close()
    assert show(tmp_path)["status"] == "ABORTED_RELEASE_UNPROVEN"


def test_abort_wrong_id_missing_attempt_or_observation_fails_closed(tmp_path, monkeypatch):
    pending_rollback(tmp_path, monkeypatch)
    assert abort(tmp_path, "f" * 32)["status"] == "UNKNOWN"
    directory = tmp_path / "akshare-observations"
    attempt_file = directory / f"reservation-attempt-{RID}.json"
    original = attempt_file.read_bytes()
    attempt_file.unlink()
    assert abort(tmp_path)["status"] == "UNKNOWN"
    attempt_file.write_bytes(original)
    attempt_file.chmod(0o600)
    intent = directory / f"intent-{OIDS[1]}.json"
    intent.write_bytes(b"existing attempt")
    intent.chmod(0o600)
    assert abort(tmp_path)["status"] == "UNKNOWN"
    assert (directory / obs._RESERVATION_PENDING).exists()
    assert show(tmp_path)["status"] == "ATTEMPT_UNKNOWN"


@pytest.mark.parametrize(
    "conflict", ["result", "partial_mapping", "foreign_role", "journal", "pending"]
)
def test_abort_conflicting_or_unprovable_evidence_never_releases(tmp_path, monkeypatch, conflict):
    pending_rollback(tmp_path, monkeypatch)
    directory = tmp_path / "akshare-observations"
    pending = directory / obs._RESERVATION_PENDING
    if conflict == "result":
        path = directory / f"result-{OIDS[0]}.json"
        path.write_bytes(b"orphan")
        path.chmod(0o600)
    elif conflict in {"partial_mapping", "foreign_role"}:
        db = sqlite3.connect(directory / obs._REGISTRY)
        try:
            if conflict == "partial_mapping":
                db.execute("INSERT INTO observation_ids VALUES(?,?)", (OIDS[0], "8" * 32))
            else:
                db.execute(
                    "INSERT INTO reservations VALUES(?,?,?,?)",
                    (OIDS[0], "8" * 32, "9" * 32, b"unrelated"),
                )
            db.commit()
        finally:
            db.close()
    elif conflict == "journal":
        path = directory / (obs._REGISTRY + "-journal")
        path.write_bytes(b"unknown")
        path.chmod(0o600)
    else:
        body, _ = obs.storage._unwrap(pending.read_bytes())
        body["requests"][0]["contract"] = "RB2702"
        pending.write_bytes(obs.storage._envelope(body))
    assert abort(tmp_path)["status"] == "UNKNOWN"
    assert pending.exists()
    calls = []
    assert attempt(tmp_path, calls)["status"] == "UNKNOWN" and not calls


def test_abort_does_not_change_already_committed_reservation(tmp_path):
    assert reserve(tmp_path)["status"] == "COMMITTED"
    assert abort(tmp_path)["status"] == "UNKNOWN"
    assert show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"
    calls = []
    from tests.unit.test_trading_calendar_akshare_reservation import binding

    assert attempt(tmp_path, calls, reservation=binding())["status"] == "ATTEMPT_UNKNOWN"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "stage", ["before_insert", "after_first_mapping", "before_commit", "after_commit"]
)
def test_abort_transaction_failure_keeps_original_pending(tmp_path, monkeypatch, stage):
    pending_rollback(tmp_path, monkeypatch)
    original = obs._registry

    class Proxy:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if stage == "before_insert" and sql.startswith("INSERT INTO reservations"):
                raise OSError("before insert")
            if stage == "before_commit" and sql == "COMMIT":
                raise OSError("before commit")
            result = self.db.execute(sql, *args)
            if stage == "after_commit" and sql == "COMMIT":
                raise OSError("after commit")
            return result

        def executemany(self, sql, args):
            if stage == "after_first_mapping":
                self.db.execute(sql, args[0])
                raise OSError("partial transaction")
            return self.db.executemany(sql, args)

    @contextlib.contextmanager
    def fault(*args):
        with original(*args) as db:
            yield Proxy(db)

    monkeypatch.setattr(obs, "_registry", fault)
    assert abort(tmp_path)["status"] == "UNKNOWN"
    monkeypatch.setattr(obs, "_registry", original)
    assert (tmp_path / "akshare-observations" / obs._RESERVATION_PENDING).exists()
    assert show(tmp_path)["status"] == (
        "ABORTED_PENDING" if stage == "after_commit" else "ATTEMPT_UNKNOWN"
    )
    calls = []
    assert attempt(tmp_path, calls)["status"] == "UNKNOWN" and not calls
    assert abort(tmp_path)["status"] == "ABORTED"
    assert_sealed(tmp_path)


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_abort_process_control_preserves_original_id_and_pending(
    tmp_path, monkeypatch, signal, stage
):
    pending_rollback(tmp_path, monkeypatch)
    original = obs._registry

    class Proxy:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if stage == "before_commit" and sql == "COMMIT":
                raise signal()
            result = self.db.execute(sql, *args)
            if stage == "after_commit" and sql == "COMMIT":
                raise signal()
            return result

        def executemany(self, *args):
            return self.db.executemany(*args)

    @contextlib.contextmanager
    def fault(*args):
        with original(*args) as db:
            yield Proxy(db)

    monkeypatch.setattr(obs, "_registry", fault)
    with pytest.raises(signal) as caught:
        abort(tmp_path)
    assert caught.value.reservation_id == RID
    assert caught.value.record_state == "COMMIT_UNKNOWN"
    monkeypatch.setattr(obs, "_registry", original)
    assert show(tmp_path)["status"] == (
        "ABORTED_PENDING" if stage == "after_commit" else "ATTEMPT_UNKNOWN"
    )
    assert (tmp_path / "akshare-observations" / obs._RESERVATION_PENDING).exists()
    assert abort(tmp_path)["status"] == "ABORTED"
    assert_sealed(tmp_path)


@pytest.mark.parametrize(
    "stage", ["sync_before_clear", "clear_before_unlink", "clear_after_unlink"]
)
def test_abort_cleanup_uncertainty_keeps_ids_sealed(tmp_path, monkeypatch, stage):
    pending_rollback(tmp_path, monkeypatch)
    original_sync, original_clear = obs._sync_registry, obs._clear_pending
    if stage == "sync_before_clear":
        monkeypatch.setattr(
            obs, "_sync_registry", lambda *args: (_ for _ in ()).throw(OSError("fsync"))
        )
    else:

        def fail_clear(*args):
            if stage == "clear_after_unlink":
                original_clear(*args)
            raise OSError("cleanup")

        monkeypatch.setattr(obs, "_clear_pending", fail_clear)
    assert abort(tmp_path)["status"] == "UNKNOWN"
    monkeypatch.setattr(obs, "_sync_registry", original_sync)
    monkeypatch.setattr(obs, "_clear_pending", original_clear)
    expected = "ABORTED_RELEASE_UNPROVEN" if stage == "clear_after_unlink" else "ABORTED_PENDING"
    assert show(tmp_path)["status"] == expected
    if stage != "clear_after_unlink":
        assert (
            obs.reconcile_reservation(root=str(tmp_path), reservation_id=RID, filesystem=FS)[
                "status"
            ]
            == "ABORTED"
        )
    assert_sealed(tmp_path)


def test_fsync_after_unlink_reports_unknown_and_release_unproven(tmp_path, monkeypatch):
    pending_rollback(tmp_path, monkeypatch)
    directory = tmp_path / "akshare-observations"
    original_fsync = os.fsync

    def fail_after_unlink(fd):
        if (
            stat.S_ISDIR(os.fstat(fd).st_mode)
            and not (directory / obs._RESERVATION_PENDING).exists()
        ):
            raise OSError("fsync after unlink")
        return original_fsync(fd)

    monkeypatch.setattr(obs.os, "fsync", fail_after_unlink)
    assert abort(tmp_path)["status"] == "UNKNOWN"
    monkeypatch.setattr(obs.os, "fsync", original_fsync)
    assert show(tmp_path)["status"] == "ABORTED_RELEASE_UNPROVEN"
    assert_sealed(tmp_path)
