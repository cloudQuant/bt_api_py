"""Read-only local receipt evidence; no source or account authentication."""

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_store as module
from bt_api_py.trading_calendar_store import CalendarStoreError, TradingCalendarStore
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_candidate_compare_current import candidate, content
from tests.unit.test_trading_calendar_policy_transaction import seed_legacy


def snapshot_files(root):
    return {
        item.relative_to(root).as_posix(): item.read_bytes()
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink()
    }


def publish(root, n=0):
    value = content()
    if n:
        value["trading_days"][0]["status"] = "closed"
    result = FixtureCalendarStore(root).publish(value, publication_id=f"p{n}", version_id=f"v{n}")
    assert result.visibility == "COMMITTED"
    return value


def hold_writer(root, ready, release):
    import fcntl

    with (Path(root) / ".lock").open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        ready.set()
        release.wait(20)


def test_receipt_consistent_snapshot_is_detached_and_read_only(tmp_path):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    value = publish(root)
    publish(root, 1)
    before = snapshot_files(root)
    store = TradingCalendarStore(root)
    current = store.read_current_with_consistent_receipts()
    assert current.publication_proof == "NOT_PROVEN"
    assert current.receipt_consistency == "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED"
    assert current.receipt_authentication == "NOT_PROVEN"
    assert (current.durability, current.source_authentication, current.identity_authentication) == (
        "UNCONFIRMED",
        "NOT_PROVEN",
        "NOT_PROVEN",
    )
    assert current.runtime_binding == "NOT_PROVEN"
    assert current.snapshot.publication_id == "p1"
    assert current.snapshot.publication_sha256 == store.read_current().publication_sha256
    current.snapshot.content["trading_days"].clear()
    assert store.read_current_with_consistent_receipts().snapshot.content["trading_days"]
    assert value["trading_days"]
    assert snapshot_files(root) == before


@pytest.mark.parametrize("missing", ["root", "lock", "versions", "publications", "receipts"])
def test_missing_store_components_never_created(tmp_path, missing):
    root = tmp_path.resolve() / "calendar"
    if missing != "root":
        root.mkdir()
        publish(root)
        if missing == "lock":
            (root / ".lock").unlink()
        else:
            directory = root / missing
            for item in directory.iterdir():
                item.unlink()
            directory.rmdir()
    before = snapshot_files(tmp_path)
    with pytest.raises(CalendarStoreError, match="^current receipt consistency unavailable$"):
        TradingCalendarStore(root).read_current_with_consistent_receipts()
    assert snapshot_files(tmp_path) == before
    if missing == "root":
        assert not root.exists()
    elif missing == "lock":
        assert not (root / ".lock").exists()
    else:
        assert not (root / missing).exists()


@pytest.mark.parametrize("pending", ["PENDING.json", "POLICY_CHANGE_PENDING.json"])
def test_both_pending_fences_block_without_modifying_disk(tmp_path, pending):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    publish(root)
    (root / pending).write_bytes(b"untrusted-intent")
    before = snapshot_files(root)
    with pytest.raises(CalendarStoreError):
        TradingCalendarStore(root).read_current_with_consistent_receipts()
    assert snapshot_files(root) == before


@pytest.mark.parametrize(
    "damage",
    ["current", "event", "version", "current_receipt", "old_receipt", "missing_old_receipt"],
)
def test_invalid_chain_or_any_receipt_blocks_read(tmp_path, damage):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    publish(root)
    publish(root, 1)
    targets = {
        "current": root / "CURRENT.json",
        "event": root / "publications/p0.json",
        "version": root / "versions/v0.json",
        "current_receipt": root / "receipts/p1.json",
        "old_receipt": root / "receipts/p0.json",
        "missing_old_receipt": root / "receipts/p0.json",
    }
    if damage == "missing_old_receipt":
        targets[damage].unlink()
    else:
        targets[damage].write_bytes(b"{}")
    before = snapshot_files(root)
    with pytest.raises(CalendarStoreError):
        TradingCalendarStore(root).read_current_with_consistent_receipts()
    assert snapshot_files(root) == before


def test_writer_lock_contention_does_not_wait_or_create_files(tmp_path):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    publish(root)
    before = snapshot_files(root)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=hold_writer, args=(str(root), ready, release))
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(CalendarStoreError):
            TradingCalendarStore(root).read_current_with_consistent_receipts()
    finally:
        release.set()
        process.join(20)
    assert process.exitcode == 0
    assert snapshot_files(root) == before


def test_symlink_or_replaced_lock_is_rejected(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    publish(root)
    store = TradingCalendarStore(root)
    prior = store._validate_chain_receipts

    def replace_lock(pointer):
        prior(pointer)
        replacement = root / "replacement"
        replacement.write_bytes((root / ".lock").read_bytes())
        os.replace(replacement, root / ".lock")

    monkeypatch.setattr(store, "_validate_chain_receipts", replace_lock)
    with pytest.raises(CalendarStoreError):
        store.read_current_with_consistent_receipts()
    (root / ".lock").unlink()
    (root / ".lock").symlink_to(root / "CURRENT.json")
    before = snapshot_files(root)
    with pytest.raises(CalendarStoreError):
        TradingCalendarStore(root).read_current_with_consistent_receipts()
    assert snapshot_files(root) == before


def test_fifo_lock_is_opened_nonblocking_and_rejected(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    publish(root)
    (root / ".lock").unlink()
    os.mkfifo(root / ".lock")
    real_open = os.open

    def check_open(path, flags, *args, **kwargs):
        if path == ".lock":
            assert flags & os.O_NONBLOCK
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", check_open)
    with pytest.raises(CalendarStoreError):
        TradingCalendarStore(root).read_current_with_consistent_receipts()


def test_current_change_during_validation_is_rejected(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    publish(root)
    store = TradingCalendarStore(root)
    prior = store._validate_chain_receipts

    def change_pointer(pointer):
        prior(pointer)
        (root / "CURRENT.json").write_bytes(b"{}")

    monkeypatch.setattr(store, "_validate_chain_receipts", change_pointer)
    with pytest.raises(CalendarStoreError):
        store.read_current_with_consistent_receipts()


@pytest.mark.parametrize("pending", ["PENDING.json", "POLICY_CHANGE_PENDING.json"])
def test_pending_appearing_during_read_is_not_cleared(tmp_path, monkeypatch, pending):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    publish(root)
    store = TradingCalendarStore(root)
    prior = store._validate_chain_receipts

    def insert_pending(pointer):
        prior(pointer)
        (root / pending).write_bytes(b"untrusted-intent")

    monkeypatch.setattr(store, "_validate_chain_receipts", insert_pending)
    with pytest.raises(CalendarStoreError):
        store.read_current_with_consistent_receipts()
    assert (root / pending).read_bytes() == b"untrusted-intent"


def test_replaced_root_is_not_accepted(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "calendar"
    old_root = tmp_path.resolve() / "original"
    root.mkdir()
    publish(root)
    store = TradingCalendarStore(root)
    prior = store._validate_chain_receipts

    def replace_root(pointer):
        prior(pointer)
        root.rename(old_root)
        root.mkdir()

    monkeypatch.setattr(store, "_validate_chain_receipts", replace_root)
    with pytest.raises(CalendarStoreError):
        store.read_current_with_consistent_receipts()
    assert (old_root / "CURRENT.json").exists()
    assert not (root / "CURRENT.json").exists()


def test_legacy_event_receipt_remains_readable(tmp_path):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    seed_legacy(TradingCalendarStore(root))
    before = snapshot_files(root)
    result = TradingCalendarStore(root).read_current_with_consistent_receipts()
    assert result.snapshot.publication_id == "old"
    assert result.publication_proof == "NOT_PROVEN"
    assert result.receipt_consistency == "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED"
    assert snapshot_files(root) == before


def test_candidate_compare_reports_only_local_receipt_proof(tmp_path):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    candidate(root, content())
    publish(root)
    before = snapshot_files(root)
    result = inbox.compare_candidate_with_current(
        root=root, candidate_id="a" * 32, filesystem=inbox.TRUSTED_LOCAL_POSIX
    )
    assert result["status"] == "PARTIAL"
    assert result["baseline"]["publication_id"] == "p0"
    assert result["baseline"]["publication_proof"] == "NOT_PROVEN"
    assert result["baseline"]["receipt_consistency"] == "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED"
    assert result["baseline"]["receipt_authentication"] == "NOT_PROVEN"
    assert result["baseline"]["source_authentication"] == "NOT_PROVEN"
    assert result["baseline"]["runtime_binding"] == "NOT_PROVEN"
    assert result["baseline"]["durability"] == "UNCONFIRMED"
    assert result["source_authentication"] == "NOT_PROVEN"
    assert snapshot_files(root) == before


def test_another_legal_receipt_observation_is_not_authenticated(tmp_path):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    candidate(root, content())
    publish(root)
    receipt_path = root / "receipts/p0.json"
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["observation"] == "POST_REPLACE_READBACK"
    receipt["observation"] = "RECOVERY_CURRENT_READBACK"
    receipt_path.write_bytes(module._canonical(receipt))
    before = snapshot_files(root)
    result = TradingCalendarStore(root).read_current_with_consistent_receipts()
    assert result.publication_proof == "NOT_PROVEN"
    assert result.receipt_consistency == "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED"
    assert result.receipt_authentication == "NOT_PROVEN"
    comparison = inbox.compare_candidate_with_current(
        root=root, candidate_id="a" * 32, filesystem=inbox.TRUSTED_LOCAL_POSIX
    )
    assert comparison["status"] == "PARTIAL"
    assert comparison["baseline"]["receipt_authentication"] == "NOT_PROVEN"
    assert comparison["baseline"]["identity_authentication"] == "NOT_PROVEN"
    assert snapshot_files(root) == before


@pytest.mark.parametrize("damage", ["receipt", "pending", "policy_pending", "lock"])
def test_candidate_compare_has_no_unproved_fallback(tmp_path, damage):
    root = tmp_path.resolve() / "calendar"
    root.mkdir()
    candidate(root, content())
    publish(root)
    if damage == "receipt":
        (root / "receipts/p0.json").unlink()
    elif damage == "lock":
        (root / ".lock").unlink()
    else:
        name = "PENDING.json" if damage == "pending" else "POLICY_CHANGE_PENDING.json"
        (root / name).write_bytes(b"untrusted-intent")
    before = snapshot_files(root)
    result = inbox.compare_candidate_with_current(
        root=root, candidate_id="a" * 32, filesystem=inbox.TRUSTED_LOCAL_POSIX
    )
    assert (result["status"], result["reason"]) == ("UNKNOWN", "BASELINE_UNAVAILABLE")
    assert "baseline" not in result
    assert snapshot_files(root) == before
