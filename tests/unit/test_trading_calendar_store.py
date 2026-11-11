"""Isolated local-filesystem checks; inputs are synthetic, not venue data."""

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from bt_api_py import trading_calendar_store as module
from bt_api_py.trading_calendar_store import CalendarStoreError, TradingCalendarStore


def content(n=0):
    return {
        "trading_days": [{"day": n}],
        "trading_hours": [{"session": n}],
        "exceptions": [{"exception": n}],
        "sources": [{"source": n}],
    }


def publish(store, n, pub=None, version=None):
    return store.publish(content(n), publication_id=pub or f"pub{n}", version_id=version or f"v{n}")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def test_publish_unchanged_and_full_snapshot(tmp_path):
    store = TradingCalendarStore(tmp_path)
    first = publish(store, 0)
    assert (first.visibility, first.durability) == ("COMMITTED", "UNCONFIRMED")
    assert store.read_current().content == content(0)
    result = publish(store, 0, "other", "other")
    assert result.visibility == "UNCHANGED"
    assert len(list((tmp_path / "versions").iterdir())) == 1
    assert len(list((tmp_path / "publications").iterdir())) == 1
    assert publish(store, 1).visibility == "COMMITTED"
    assert store.read_current().sequence == 2
    assert store.verify_publication("pub0") == "UNKNOWN"
    assert store.verify_publication("pub1") == "VISIBLE"


def test_reused_publication_id_requires_same_content_and_proven_receipt(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    assert publish(store, 0).visibility == "COMMITTED"
    conflict = publish(store, 1, pub="pub0", version="v0")
    assert conflict.visibility == "ID_CONFLICT"
    assert "conflicts" in conflict.detail
    assert publish(store, 0, pub="pub0", version="different").visibility == "ID_CONFLICT"
    assert publish(store, 1).visibility == "COMMITTED"
    assert publish(store, 0).visibility == "HISTORICAL_COMMITTED"
    assert publish(store, 1, pub="pub0", version="v0").visibility == "ID_CONFLICT"
    (tmp_path / "receipts" / "pub0.json").unlink()
    result = publish(store, 1, pub="pub0", version="v0")
    assert result.visibility == "COMMIT_UNKNOWN"
    assert "without a proven receipt" in result.detail
    assert len(list((tmp_path / "versions").iterdir())) == 2


def test_verify_holds_writer_lock_across_read(tmp_path, monkeypatch):
    reader = TradingCalendarStore(tmp_path)
    writer = TradingCalendarStore(tmp_path)
    assert publish(writer, 0).visibility == "COMMITTED"
    read = reader.read_current
    blocked = []

    def attempt_concurrent_publish():
        snapshot = read()
        blocked.append(publish(writer, 1).visibility)
        return snapshot

    monkeypatch.setattr(reader, "read_current", attempt_concurrent_publish)
    assert reader.verify_publication("pub0") == "VISIBLE"
    assert blocked == ["COMMIT_UNKNOWN"]
    assert publish(writer, 1, pub="new1", version="new1").visibility == "COMMITTED"
    assert reader.verify_publication("pub0") == "UNKNOWN"


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {**content(), "extra": []},
        {**content(), "sources": None},
        {**content(), "sources": [float("nan")]},
        {**content(), "sources": [{1: "bad"}]},
    ],
)
def test_invalid_input(tmp_path, invalid):
    result = TradingCalendarStore(tmp_path).publish(invalid, publication_id="p", version_id="v")
    assert result.visibility == "NOT_COMMITTED"
    assert not (tmp_path / "CURRENT.json").exists()


def test_identifiers_and_root(tmp_path):
    store = TradingCalendarStore(tmp_path)
    for bad in ("../escape", "", ".", "x/y", "a" * 129, 42):
        assert (
            store.publish(content(), publication_id=bad, version_id="v").visibility
            == "NOT_COMMITTED"
        )
    with pytest.raises(CalendarStoreError):
        TradingCalendarStore(Path("relative"))
    with pytest.raises(CalendarStoreError):
        TradingCalendarStore(tmp_path / ".." / "unsafe")


@pytest.mark.parametrize(
    "target", ["pointer", "event", "snapshot", "missing", "unknown_schema", "duplicate_key"]
)
def test_corruption_is_rejected(tmp_path, target):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    pointer = tmp_path / "CURRENT.json"
    event = tmp_path / "publications" / "pub0.json"
    snapshot = tmp_path / "versions" / "v0.json"
    if target == "pointer":
        pointer.write_bytes(b"{}")
    elif target == "event":
        event.write_bytes(event.read_bytes() + b" ")
    elif target == "snapshot":
        value = json.loads(snapshot.read_bytes())
        value["content"]["trading_days"] = []
        snapshot.write_bytes(canonical(value))
    elif target == "missing":
        snapshot.unlink()
    elif target == "unknown_schema":
        value = json.loads(snapshot.read_bytes())
        value["schema"] = "unknown"
        snapshot.write_bytes(canonical(value))
    else:
        pointer.write_bytes(b'{"schema":"x","schema":"y"}')
    with pytest.raises(CalendarStoreError):
        store.read_current()
    assert store.verify_publication("pub0") == "UNKNOWN"
    assert publish(store, 1).visibility == "NOT_COMMITTED"


def test_missing_current_does_not_select_orphans(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    (tmp_path / "CURRENT.json").unlink()
    assert publish(store, 1).visibility == "NOT_COMMITTED"
    with pytest.raises(CalendarStoreError):
        store.read_current()


def test_symlinked_current_is_rejected(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    pointer = tmp_path / "CURRENT.json"
    copied_pointer = tmp_path / "copy.json"
    copied_pointer.write_bytes(pointer.read_bytes())
    pointer.unlink()
    pointer.symlink_to(copied_pointer)
    with pytest.raises(CalendarStoreError):
        store.read_current()
    assert publish(store, 1).visibility == "NOT_COMMITTED"


def test_version_and_event_never_overwritten(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    version = tmp_path / "versions" / "v0.json"
    event = tmp_path / "publications" / "pub0.json"
    original_version, original_event = version.read_bytes(), event.read_bytes()
    assert publish(store, 1, version="v0").visibility == "NOT_COMMITTED"
    assert publish(store, 2, pub="pub0").visibility == "ID_CONFLICT"
    assert version.read_bytes() == original_version
    assert event.read_bytes() == original_event
    assert store.read_current().content == content(0)


def test_failed_before_replace_preserves_old_snapshot(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    original = store._write_new

    def fail_event(path, data):
        if path.parent.name == "publications":
            raise OSError("injected")
        original(path, data)

    monkeypatch.setattr(store, "_write_new", fail_event)
    assert publish(store, 1).visibility == "NOT_COMMITTED"
    assert store.read_current().content == content(0)


def test_replace_can_commit_then_raise(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    replace = module.os.replace

    def uncertain(source, target):
        replace(source, target)
        raise OSError("injected after replace")

    monkeypatch.setattr(module.os, "replace", uncertain)
    result = publish(store, 1)
    assert result.visibility == "COMMIT_UNKNOWN"
    assert store.verify_publication(result.publication_id) == "UNKNOWN"
    assert store.read_current().content == content(1)


def test_post_replace_readback_failure_is_unknown(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    original = store._read
    calls = 0

    def fail_readback(path):
        nonlocal calls
        if path.name == "CURRENT.json":
            calls += 1
            if calls == 2:
                raise OSError("injected readback")
        return original(path)

    monkeypatch.setattr(store, "_read", fail_readback)
    assert publish(store, 1).visibility == "COMMIT_UNKNOWN"
    assert store.verify_publication("pub1") == "UNKNOWN"


def _interrupted_publisher(root, stage, results):
    store = TradingCalendarStore(root)
    replace = module.os.replace

    def interrupt(source, target):
        if stage == "before":
            os._exit(72)
        replace(source, target)
        if stage == "crash":
            os._exit(73)
        raise OSError("injected after replacement")

    module.os.replace = interrupt
    results.put(publish(store, 1).visibility)


def _crash_at_intent_cleanup(root, remove):
    store = TradingCalendarStore(root)
    clear = store._clear_pending

    def crash():
        if remove:
            clear()
        os._exit(74 if remove else 75)

    store._clear_pending = crash
    publish(store, 1)


@pytest.mark.parametrize("remove", [False, True])
def test_crash_after_receipt_supports_same_id_reconciliation(tmp_path, remove):
    assert publish(TradingCalendarStore(tmp_path), 0).visibility == "COMMITTED"
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=_crash_at_intent_cleanup, args=(tmp_path, remove))
    proc.start()
    proc.join(15)
    assert proc.exitcode == (74 if remove else 75)
    assert (tmp_path / "receipts" / "pub1.json").exists()
    restarted = TradingCalendarStore(tmp_path)
    if remove:
        # The writer died after deleting PENDING but before returning to its caller.
        assert publish(restarted, 2).visibility == "COMMITTED"
        proof = restarted.reconcile_publication("pub1")
        assert proof.visibility == "HISTORICAL_COMMITTED"
    else:
        assert publish(restarted, 2).fence == "PRESENT"
        proof = restarted.reconcile_publication("pub1")
        assert proof.visibility == "COMMITTED"
        assert proof.fence == "CLEAR"
        assert publish(restarted, 2).visibility == "COMMITTED"
        assert restarted.reconcile_publication("pub1").visibility == "HISTORICAL_COMMITTED"
    assert proof.durability == "UNCONFIRMED"


@pytest.mark.parametrize("damage", ["corrupt", "chain", "orphan"])
def test_invalid_receipt_or_chain_never_proves_or_clears_intent(tmp_path, monkeypatch, damage):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    monkeypatch.setattr(store, "_clear_pending", lambda: (_ for _ in ()).throw(OSError("stop")))
    assert publish(store, 1).visibility == "COMMITTED"
    receipt = tmp_path / "receipts" / "pub1.json"
    if damage == "corrupt":
        receipt.write_bytes(b"{}")
    elif damage == "chain":
        (tmp_path / "publications" / "pub0.json").unlink()
    else:
        # An intact receipt and prewritten event cannot establish an orphan committed.
        prior = json.loads((tmp_path / "publications" / "pub0.json").read_bytes())
        prior_event = (tmp_path / "publications" / "pub0.json").read_bytes()
        import hashlib

        (tmp_path / "CURRENT.json").write_bytes(
            canonical(
                {
                    "schema": module._POINTER,
                    "publication_id": prior["publication_id"],
                    "publication_sha256": hashlib.sha256(prior_event).hexdigest(),
                }
            )
        )
    restarted = TradingCalendarStore(tmp_path)
    assert restarted.reconcile_publication("pub1").visibility == "COMMIT_UNKNOWN"
    assert (tmp_path / "PENDING.json").exists()
    assert publish(restarted, 2).fence == "PRESENT"


@pytest.mark.parametrize("stage", ["raise", "crash"])
def test_process_interrupted_after_replace_recovers_visible_receipt(tmp_path, stage):
    assert publish(TradingCalendarStore(tmp_path), 0).visibility == "COMMITTED"
    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    proc = ctx.Process(target=_interrupted_publisher, args=(tmp_path, stage, results))
    proc.start()
    proc.join(15)
    assert proc.exitcode == (73 if stage == "crash" else 0)
    if stage == "raise":
        assert results.get(timeout=5) == "COMMIT_UNKNOWN"
    restarted = TradingCalendarStore(tmp_path)
    repeated = publish(restarted, 1)
    assert (repeated.visibility, repeated.fence) == ("COMMIT_UNKNOWN", "PRESENT")
    invalid_repeat = restarted.publish({}, publication_id="pub1", version_id="v1")
    assert (invalid_repeat.visibility, invalid_repeat.fence) == ("COMMIT_UNKNOWN", "PRESENT")
    blocked = publish(restarted, 1, "different", "different")
    assert (blocked.visibility, blocked.fence) == ("NOT_COMMITTED", "PRESENT")
    assert restarted.reconcile_publication("different").visibility == "COMMIT_UNKNOWN"
    assert (tmp_path / "PENDING.json").exists()
    proof = restarted.reconcile_publication("pub1")
    assert (proof.visibility, proof.fence) == ("COMMITTED", "CLEAR")
    receipt = json.loads((tmp_path / "receipts" / "pub1.json").read_bytes())
    assert receipt["observation"] == "RECOVERY_CURRENT_READBACK"
    assert publish(restarted, 2).visibility == "COMMITTED"
    assert restarted.reconcile_publication("pub1").visibility == "HISTORICAL_COMMITTED"


def test_process_crash_before_replace_remains_blocked(tmp_path):
    assert publish(TradingCalendarStore(tmp_path), 0).visibility == "COMMITTED"
    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    proc = ctx.Process(target=_interrupted_publisher, args=(tmp_path, "before", results))
    proc.start()
    proc.join(15)
    assert proc.exitcode == 72
    restarted = TradingCalendarStore(tmp_path)
    assert restarted.read_current().content == content(0)
    assert restarted.reconcile_publication("pub1").visibility == "COMMIT_UNKNOWN"
    # Neither identical current content nor a different ID may bypass the fence.
    assert publish(restarted, 0, "other", "other").fence == "PRESENT"
    assert publish(restarted, 2).fence == "PRESENT"


def test_recovery_receipt_write_failure_keeps_intent(tmp_path, monkeypatch):
    assert publish(TradingCalendarStore(tmp_path), 0).visibility == "COMMITTED"
    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    proc = ctx.Process(target=_interrupted_publisher, args=(tmp_path, "crash", results))
    proc.start()
    proc.join(15)
    assert proc.exitcode == 73
    store = TradingCalendarStore(tmp_path)

    def fail_write(path, data):
        raise OSError("injected receipt write failure")

    monkeypatch.setattr(store, "_write_new", fail_write)
    assert store.reconcile_publication("pub1").visibility == "COMMIT_UNKNOWN"
    assert (tmp_path / "PENDING.json").exists()
    assert TradingCalendarStore(tmp_path).reconcile_publication("pub1").visibility == "COMMITTED"


def test_missing_receipt_without_intent_cannot_prove_history(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    assert publish(store, 1).visibility == "COMMITTED"
    (tmp_path / "receipts" / "pub0.json").unlink()
    assert store.reconcile_publication("pub0").visibility == "COMMIT_UNKNOWN"


def test_partial_intent_blocks_without_guessing(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    write = store._write_new

    def fail_partial(path, data):
        if path.name == "PENDING.json":
            write(path, b"{")
            raise OSError("partial intent")
        write(path, data)

    monkeypatch.setattr(store, "_write_new", fail_partial)
    assert publish(store, 1).visibility == "NOT_COMMITTED"
    restarted = TradingCalendarStore(tmp_path)
    assert restarted.reconcile_publication("pub1").visibility == "COMMIT_UNKNOWN"
    blocked = publish(restarted, 0, "other", "other")
    assert (blocked.visibility, blocked.fence) == ("COMMIT_UNKNOWN", "PRESENT")


@pytest.mark.parametrize("pending", [b'{"publication_id":"another"}', b"{}", b"[]", b"{"])
def test_malformed_pending_does_not_identify_owner(tmp_path, pending):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    (tmp_path / "PENDING.json").write_bytes(pending)
    for publication_id in ("pub0", "another", "new"):
        result = store.publish({}, publication_id=publication_id, version_id="v")
        assert (result.visibility, result.fence) == ("COMMIT_UNKNOWN", "PRESENT")


def test_delete_failure_reports_visible_post_commit_incomplete(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"

    def fail_delete():
        raise OSError("injected unlink failure")

    monkeypatch.setattr(store, "_clear_pending", fail_delete)
    result = publish(store, 1)
    assert (result.visibility, result.post_commit, result.fence) == (
        "COMMITTED",
        "INCOMPLETE",
        "UNKNOWN",
    )
    assert store.reconcile_publication("pub1").post_commit == "INCOMPLETE"
    restarted = TradingCalendarStore(tmp_path)
    assert publish(restarted, 2).fence == "PRESENT"
    assert restarted.reconcile_publication("pub1").fence == "CLEAR"
    assert publish(restarted, 2).visibility == "COMMITTED"


def test_error_after_delete_is_committed_but_cleanup_uncertain(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    clear = store._clear_pending

    def delete_then_fail():
        clear()
        raise OSError("after unlink")

    monkeypatch.setattr(store, "_clear_pending", delete_then_fail)
    result = publish(store, 1)
    assert (result.visibility, result.post_commit, result.fence) == (
        "COMMITTED",
        "INCOMPLETE",
        "UNKNOWN",
    )
    restarted = TradingCalendarStore(tmp_path)
    assert restarted.reconcile_publication("pub1").visibility == "COMMITTED"
    assert publish(restarted, 2).visibility == "COMMITTED"


def test_corrupt_pending_target_remains_blocked(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    monkeypatch.setattr(store, "_clear_pending", lambda: (_ for _ in ()).throw(OSError("stop")))
    assert publish(store, 1).visibility == "COMMITTED"
    (tmp_path / "versions" / "v1.json").write_bytes(b"{}")
    restarted = TradingCalendarStore(tmp_path)
    assert restarted.reconcile_publication("pub1").visibility == "COMMIT_UNKNOWN"
    assert publish(restarted, 2).fence == "PRESENT"


def test_reader_keeps_pointer_across_concurrent_publication(tmp_path, monkeypatch):
    reader = TradingCalendarStore(tmp_path)
    writer = TradingCalendarStore(tmp_path)
    assert publish(writer, 0).visibility == "COMMITTED"
    original = reader._read
    published = False

    def switch_after_pointer(path):
        nonlocal published
        data = original(path)
        if path.name == "CURRENT.json" and not published:
            published = True
            assert publish(writer, 1).visibility == "COMMITTED"
        return data

    monkeypatch.setattr(reader, "_read", switch_after_pointer)
    snapshot = reader.read_current()
    assert (snapshot.version_id, snapshot.content) == ("v0", content(0))
    assert writer.read_current().version_id == "v1"


def _hold_lock(root, ready, release):
    store = TradingCalendarStore(root)
    with store._writer_lock():
        ready.set()
        if not release.wait(10):
            raise RuntimeError("test timed out")


def test_actual_process_lock(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    proc = ctx.Process(target=_hold_lock, args=(tmp_path, ready, release))
    proc.start()
    try:
        assert ready.wait(10)
        assert publish(TradingCalendarStore(tmp_path), 0).visibility == "COMMIT_UNKNOWN"
        assert (
            TradingCalendarStore(tmp_path).reconcile_publication("pub0").visibility
            == "COMMIT_UNKNOWN"
        )
    finally:
        release.set()
        proc.join(10)
    assert proc.exitcode == 0
    assert publish(TradingCalendarStore(tmp_path), 0).visibility == "COMMITTED"


def _read_loop(root, start, done, ready, results):
    store = TradingCalendarStore(root)
    store.read_current()
    ready.put(True)
    start.wait(10)
    try:
        count = 0
        while not done.is_set() or count < 30:
            snap = store.read_current()
            number = snap.content["trading_days"][0]["day"]
            if snap.content != content(number) or snap.version_id != f"v{number}":
                raise AssertionError("mixed generations")
            count += 1
        results.put((True, count))
    except Exception as exc:
        results.put((False, repr(exc)))


def test_concurrent_process_readers_observe_complete_versions(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert publish(store, 0).visibility == "COMMITTED"
    ctx = multiprocessing.get_context("spawn")
    start, done, ready, results = ctx.Event(), ctx.Event(), ctx.Queue(), ctx.Queue()
    readers = [
        ctx.Process(target=_read_loop, args=(tmp_path, start, done, ready, results))
        for _ in range(4)
    ]
    for reader in readers:
        reader.start()
    try:
        assert all(ready.get(timeout=15) for _ in readers)
        start.set()
        for n in range(1, 16):
            assert publish(store, n).visibility == "COMMITTED"
    finally:
        done.set()
        for reader in readers:
            reader.join(20)
            if reader.is_alive():
                reader.terminate()
                reader.join()
    assert all(reader.exitcode == 0 for reader in readers)
    assert all(results.get(timeout=5)[0] for _ in readers)
