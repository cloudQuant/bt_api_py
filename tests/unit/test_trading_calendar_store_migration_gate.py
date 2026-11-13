"""Store-level migration fencing using only synthetic local directories."""

import hashlib
import json
import os

import pytest

from bt_api_py import trading_calendar_migration_barrier as barrier
from bt_api_py import trading_calendar_store as store_module
from bt_api_py.trading_calendar_store import (
    CalendarStoreError,
    CurrentPrecondition,
    TradingCalendarStore,
)
from tests.unit.test_trading_calendar_policy_transaction import claim, content, publish, switch


def envelope(body):
    def encode(value):
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()

    return encode({"body": body, "sha256": hashlib.sha256(encode(body)).hexdigest()})


def ready_head(state="READY"):
    head = {
        "schema": "bt_api.calendar-scope-check-head/2",
        "state": state,
        "migration_id": "a" * 32,
        "intent_sha256": "b" * 64,
        "environment": "SIMULATION_ONLY",
        "old_writers": "OLD_WRITERS_STOPPED_DECLARED_NOT_VERIFIED",
    }
    if state == "READY":
        head["database_id"] = "c" * 32
    return head


def put(root, name, raw):
    folder = root / "observation-runs"
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / name
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_no_barrier_legacy_and_recognized_heads_preserve_writes(tmp_path):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    assert publish(store, policy).visibility == "COMMITTED"
    old = store.read_current()
    for n, head in enumerate(
        (
            {
                "schema": "bt_api.calendar-scope-check-head/1",
                "sequence": 1,
                "record_sha256": "b" * 64,
                "batch_id": "a" * 32,
            },
            ready_head(),
        ),
        start=1,
    ):
        put(tmp_path, "scope-check-head.json", envelope(head))
        expected = CurrentPrecondition.matching(old.publication_id, old.publication_sha256)
        assert publish(store, policy, n, expected=expected).visibility == "COMMITTED"
        old = store.read_current()
    assert store.reconcile_publication("p0").visibility == "HISTORICAL_COMMITTED"
    assert switch(store, policy.revision, "manual", "op1").status == "COMMITTED"


def test_original_policy_id_replay_is_unknown_during_barrier(tmp_path):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    assert switch(store, policy.revision, "manual", "prior_op").status == "COMMITTED"
    assert switch(store, policy.revision, "manual", "prior_op").status == "COMMITTED"
    put(tmp_path, "scope-check-migration-pending.json", b"pending")
    result = store.change_review_policy(
        expected_revision=policy.revision,
        mode="manual",
        operation_id="prior_op",
        actor="operator1",
        at_utc="2026-01-01T00:00:00.000000Z",
        locked_guard=lambda _: None,
    )
    assert (result.status, result.operation_id) == ("COMMIT_UNKNOWN", "prior_op")
    result = store.reconcile_review_policy_change("prior_op", locked_guard=lambda *_: None)
    assert (result.status, result.operation_id) == ("COMMIT_UNKNOWN", "prior_op")
    assert store.read_policy_change("prior_op")["operation_id"] == "prior_op"


@pytest.mark.parametrize(
    "damage",
    [
        "pending",
        "pending_bad",
        "pending_symlink",
        "dir_symlink",
        "head_bad",
        "head_migrating",
        "head_symlink",
    ],
)
def test_all_direct_store_write_entries_fail_closed_and_preserve_ids(tmp_path, damage):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    assert publish(store, policy).visibility == "COMMITTED"
    snapshot = store.read_current()
    expected = CurrentPrecondition.matching(snapshot.publication_id, snapshot.publication_sha256)
    assert publish(store, policy, 1, expected=expected).visibility == "COMMITTED"
    first = store.read_current()
    if damage == "dir_symlink":
        (tmp_path / "observation-runs").symlink_to(tmp_path, target_is_directory=True)
    elif damage in {"pending", "pending_bad"}:
        put(
            tmp_path,
            "scope-check-migration-pending.json",
            envelope({"migration_id": "a" * 32}) if damage == "pending" else b"bad",
        )
    elif damage == "pending_symlink":
        folder = tmp_path / "observation-runs"
        folder.mkdir(mode=0o700)
        (folder / "scope-check-migration-pending.json").symlink_to(tmp_path / "CURRENT.json")
    elif damage == "head_symlink":
        folder = tmp_path / "observation-runs"
        folder.mkdir(mode=0o700)
        (folder / "scope-check-head.json").symlink_to(tmp_path / "CURRENT.json")
    else:
        put(
            tmp_path,
            "scope-check-head.json",
            b"bad" if damage == "head_bad" else envelope(ready_head("MIGRATING")),
        )
    before = files(tmp_path)
    with pytest.raises(CalendarStoreError):
        store.initialize_review_policy(config_read_succeeded=True)
    for republish in (False, True):
        result = store.publish(
            content(),
            publication_id="p0",
            version_id="v0",
            republish=republish,
            expected_current=CurrentPrecondition.absent(),
            policy_publication=claim(policy),
            locked_policy_guard=lambda _: None,
        )
        assert (result.visibility, result.publication_id, result.version_id) == (
            "COMMIT_UNKNOWN",
            "p0",
            "v0",
        )
    result = store.reconcile_publication("p0")
    assert (result.visibility, result.publication_id) == ("COMMIT_UNKNOWN", "p0")
    result = switch(store, policy.revision, "manual", "original_op")
    assert (result.status, result.operation_id) == ("COMMIT_UNKNOWN", "original_op")
    result = store.reconcile_review_policy_change("original_op")
    assert (result.status, result.operation_id) == ("COMMIT_UNKNOWN", "original_op")
    assert store.read_current() == first
    assert store.verify_publication("p1") == "VISIBLE"
    assert store.read_publication_policy("p0") is not None
    assert files(tmp_path) == before


def test_interrupted_publication_and_policy_conflicts_are_not_cleared(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    assert publish(store, policy).visibility == "COMMITTED"
    first = store.read_current()
    expected = CurrentPrecondition.matching(first.publication_id, first.publication_sha256)
    original_replace = store_module.os.replace

    def interrupted(src, dst):
        original_replace(src, dst)
        raise OSError("simulated post-replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(store_module.os, "replace", interrupted)
        assert publish(store, policy, 1, expected=expected).visibility == "COMMIT_UNKNOWN"
    pending = (tmp_path / "PENDING.json").read_bytes()
    put(tmp_path, "scope-check-migration-pending.json", b"pending")
    result = store.reconcile_publication("p1")
    assert (result.visibility, result.publication_id) == ("COMMIT_UNKNOWN", "p1")
    assert (tmp_path / "PENDING.json").read_bytes() == pending
    assert store.verify_publication("p1") == "UNKNOWN"
    (tmp_path / "observation-runs/scope-check-migration-pending.json").unlink()
    assert store.reconcile_publication("p1").visibility == "COMMITTED"
    changed = store.change_review_policy(
        expected_revision=policy.revision,
        mode="manual",
        operation_id="policy1",
        actor="operator1",
        at_utc="2026-01-01T00:00:00.000000Z",
        prepare_only=True,
    )
    assert changed.status == "PENDING_CONFIG"
    policy_pending = (tmp_path / "POLICY_CHANGE_PENDING.json").read_bytes()
    put(tmp_path, "scope-check-migration-pending.json", b"pending")
    assert store.reconcile_review_policy_change("policy1").status == "COMMIT_UNKNOWN"
    assert (tmp_path / "POLICY_CHANGE_PENDING.json").read_bytes() == policy_pending
    with pytest.raises(CalendarStoreError, match="pending policy change"):
        store.read_review_policy()


def test_lock_and_directory_replacement_fail_closed(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    put(tmp_path, "scope-check-head.json", envelope(ready_head()))
    original = barrier.os.stat

    def replacing(path, *args, **kwargs):
        if path == "scope-check-head.json" and kwargs.get("dir_fd") is not None:
            folder = tmp_path / "observation-runs"
            folder.rename(tmp_path / "old-runs")
            folder.mkdir(mode=0o700)
            monkeypatch.setattr(barrier.os, "stat", original)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(barrier.os, "stat", replacing)
    assert switch(store, policy.revision, "manual", "op1").status == "COMMIT_UNKNOWN"
    assert not (tmp_path / "POLICY_CHANGE_PENDING.json").exists()


def test_hardlinked_ready_head_is_unknown_with_original_id(tmp_path):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    outside = tmp_path.parent / (tmp_path.name + "-external-head.json")
    outside.write_bytes(envelope(ready_head()))
    outside.chmod(0o600)
    folder = tmp_path / "observation-runs"
    folder.mkdir(mode=0o700)
    os.link(outside, folder / "scope-check-head.json")
    result = switch(store, policy.revision, "manual", "original_op")
    assert (result.status, result.operation_id) == ("COMMIT_UNKNOWN", "original_op")
    assert outside.stat().st_nlink == 2
    assert not (tmp_path / "POLICY_CHANGE_PENDING.json").exists()


@pytest.mark.parametrize("initial", ["ready", "absent", "link_added"])
def test_head_changes_during_final_pending_check(tmp_path, monkeypatch, initial):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    folder = tmp_path / "observation-runs"
    folder.mkdir(mode=0o700)
    head = folder / "scope-check-head.json"
    if initial != "absent":
        head.write_bytes(envelope(ready_head()))
        head.chmod(0o600)
    replacement = folder / "replacement.json"
    replacement.write_bytes(envelope(ready_head("MIGRATING")))
    replacement.chmod(0o600)
    original = barrier.os.stat
    pending_checks = 0

    def replace_head(path, *args, **kwargs):
        nonlocal pending_checks
        if path == "scope-check-migration-pending.json" and kwargs.get("dir_fd") is not None:
            pending_checks += 1
            if pending_checks == 3:
                if initial == "link_added":
                    os.link(head, tmp_path.parent / (tmp_path.name + "-late-link.json"))
                else:
                    os.replace(replacement, head)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(barrier.os, "stat", replace_head)
    result = switch(store, policy.revision, "manual", "original_op")
    assert pending_checks == 3
    assert (result.status, result.operation_id) == ("COMMIT_UNKNOWN", "original_op")
    assert not (tmp_path / "POLICY_CHANGE_PENDING.json").exists()


@pytest.mark.parametrize("failure", ["permission", "lock_replace", "root_replace", "appeared"])
def test_unstable_absence_and_lock_identity_fail_closed(tmp_path, monkeypatch, failure):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    original = barrier.os.stat
    count = 0

    def fail(path, *args, **kwargs):
        nonlocal count
        if path == "observation-runs" and kwargs.get("dir_fd") is not None:
            count += 1
            if failure == "permission":
                raise PermissionError("test denied")
            if failure == "appeared" and count == 1:
                return original(path, *args, **kwargs)
            monkeypatch.setattr(barrier.os, "stat", original)
            if failure == "appeared" and count == 2:
                (tmp_path / "observation-runs").mkdir(mode=0o700)
            if failure == "lock_replace":
                (tmp_path / ".lock").rename(tmp_path / "old-lock")
                (tmp_path / ".lock").touch(mode=0o600)
            if failure == "root_replace":
                tmp_path.rename(tmp_path.with_name(tmp_path.name + "-old"))
                tmp_path.mkdir()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(barrier.os, "stat", fail)
    result = switch(store, policy.revision, "manual", "op1")
    assert (result.status, result.operation_id) == ("COMMIT_UNKNOWN", "op1")


@pytest.mark.parametrize("conflict", [None, "PENDING.json", "POLICY_CHANGE_PENDING.json"])
def test_real_migration_under_store_lock_and_conflicts(tmp_path, conflict):
    from bt_api_py import trading_calendar_scope_index as index

    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    assert publish(store, policy).visibility == "COMMITTED"
    (tmp_path / "observation-runs").mkdir(mode=0o700)

    class Operator:
        root = str(tmp_path)
        run_id = "a" * 32
        _store = store

        def _guard(self):
            pass

        def _check_policy_locked(self, policy, **kwargs):
            assert policy is not None

    operator = Operator()
    migration_id = "e" * 32
    started = index.begin(
        operator=operator, migration_id=migration_id, legacy_ids=[], declaration=index._DECLARATION
    )
    assert started["status"] == "PENDING_MIGRATION"
    assert (
        index.show_migration(operator=operator, migration_id=migration_id)["status"]
        == "PENDING_MIGRATION"
    )
    pending = tmp_path / "observation-runs/scope-check-migration-pending.json"
    original = pending.read_bytes()
    if conflict:
        (tmp_path / conflict).write_bytes(b"legacy writer pending evidence")
    resumed = index.resume(
        operator=operator, migration_id=migration_id, declaration=index._DECLARATION
    )
    if conflict:
        assert resumed["status"] == "UNKNOWN"
        assert resumed["migration_id"] == migration_id
        assert pending.read_bytes() == original
        assert (tmp_path / conflict).read_bytes() == b"legacy writer pending evidence"
        assert (
            index.show_migration(operator=operator, migration_id=migration_id)["status"]
            == "PENDING_MIGRATION"
        )
    else:
        assert resumed["reason"] == "MIGRATION_READY"
        assert not pending.exists()
        assert (
            index.show_migration(operator=operator, migration_id=migration_id)["status"]
            == "VISIBLE_UNAUTHENTICATED"
        )
        assert switch(store, policy.revision, "manual", "after_migration").status == "COMMITTED"
