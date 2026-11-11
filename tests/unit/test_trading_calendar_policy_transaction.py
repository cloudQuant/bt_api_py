"""Cooperating local writers only; no exchange or identity is authenticated."""

import hashlib
import json
import multiprocessing
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bt_api_py import trading_calendar_store as module
from bt_api_py.trading_calendar_store import (
    CalendarStoreError,
    CurrentPrecondition,
    PolicyPublication,
    TradingCalendarStore,
    calendar_snapshot_digest,
)


def content(n=0):
    return {"trading_days": [{"n": n}], "trading_hours": [], "exceptions": [], "sources": []}


def claim(policy, n=0, difference=None, expected=None):
    difference = {"changed": n} if difference is None else difference
    snapshot_hash = calendar_snapshot_digest(content(n))
    diff_hash = hashlib.sha256(module._canonical(difference)).hexdigest()
    return PolicyPublication(
        policy.directory_id,
        policy.revision,
        policy.mode,
        snapshot_hash,
        difference,
        diff_hash,
        "reviewer" if policy.mode == "manual" else None,
        snapshot_hash if policy.mode == "manual" else None,
        diff_hash if policy.mode == "manual" else None,
        policy.revision if policy.mode == "manual" else None,
        (expected or CurrentPrecondition.absent()) if policy.mode == "manual" else None,
        "2026-01-01T00:00:00.000000Z" if policy.mode == "manual" else None,
    )


def publish(store, policy, n=0, *, expected=None, evidence=None, publication_id=None):
    return store.publish(
        content(n),
        publication_id=publication_id or f"p{n}",
        version_id=f"v{n}",
        expected_current=expected or CurrentPrecondition.absent(),
        policy_publication=evidence or claim(policy, n, expected=expected),
    )


def artifact_paths(root):
    return sorted(
        p.relative_to(root).as_posix()
        for d in ("versions", "publications", "receipts")
        for p in (root / d).iterdir()
    )


def switch(store, revision, mode, operation_id=None):
    return store.change_review_policy(
        expected_revision=revision,
        mode=mode,
        operation_id=operation_id or uuid.uuid4().hex,
        actor="operator1",
        at_utc="2026-01-01T00:00:00.000000Z",
    )


def seed_legacy(store):
    """Construct a historical /1 receipt without invoking a current write API."""
    snapshot = module._canonical({"schema": module._SNAPSHOT, "content": content()})
    event = module._canonical(
        {
            "schema": module._EVENT,
            "publication_id": "old",
            "sequence": 1,
            "previous": None,
            "version_id": "old",
            "content_sha256": module._sha(module._content(content())),
        }
    )
    pointer = {
        "schema": module._POINTER,
        "publication_id": "old",
        "publication_sha256": module._sha(event),
    }
    receipt = module._canonical(
        {
            "schema": module._RECEIPT,
            "publication_id": "old",
            "version_id": "old",
            "pointer": pointer,
            "observation": "POST_REPLACE_READBACK",
        }
    )
    with store._writer_lock():
        store._write_new(store.root / "versions/old.json", snapshot)
        store._write_new(store.root / "publications/old.json", event)
        store._write_new(store.root / "receipts/old.json", receipt)
        store._write_new(store.root / "CURRENT.json", module._canonical(pointer))


def test_explicit_initialization_and_manual_auto_switch_never_publishes(tmp_path):
    store = TradingCalendarStore(tmp_path)
    with pytest.raises(CalendarStoreError):
        store.initialize_review_policy(config_read_succeeded=False)
    assert not (tmp_path / "REVIEW_POLICY_INITIALIZED.json").exists()
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    assert policy.mode == "manual" and policy.mode_source == "explicit"
    changed = switch(store, policy.revision, "auto")
    assert changed.status == "COMMITTED"
    auto = changed.policy
    assert auto.mode == "auto" and auto.revision != policy.revision
    assert store.read_review_policy() == auto
    assert not (tmp_path / "CURRENT.json").exists()
    assert artifact_paths(tmp_path) == []
    assert publish(store, policy).visibility == "NOT_COMMITTED"


def test_new_publication_requires_explicitly_initialized_policy(tmp_path):
    store = TradingCalendarStore(tmp_path)
    assert (
        store.publish(content(), publication_id="p0", version_id="v0").visibility == "NOT_COMMITTED"
    )
    assert not (tmp_path / "CURRENT.json").exists()
    assert artifact_paths(tmp_path) == []


def test_manual_unchanged_needs_valid_policy_snapshot_and_current_but_no_review(tmp_path):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    assert publish(store, policy).visibility == "COMMITTED"
    current = store.read_current()
    baseline = CurrentPrecondition.matching(current.publication_id, current.publication_sha256)
    pending = replace(
        claim(policy, expected=baseline),
        reviewer=None,
        confirmation_snapshot_sha256=None,
        confirmation_difference_sha256=None,
        confirmation_policy_revision=None,
        confirmation_current=None,
        confirmation_utc=None,
    )
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = store.publish(
        content(),
        publication_id="noop",
        version_id="unused",
        expected_current=baseline,
        policy_publication=pending,
    )
    assert (result.visibility, result.version_id, result.durability) == (
        "UNCHANGED",
        "v0",
        "UNCONFIRMED",
    )
    assert {
        p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before
    stale_current = replace(pending, confirmation_current=None)
    assert (
        store.publish(
            content(),
            publication_id="stale",
            version_id="stale",
            expected_current=CurrentPrecondition.absent(),
            policy_publication=stale_current,
        ).visibility
        == "NOT_COMMITTED"
    )
    assert (
        store.publish(
            content(),
            publication_id="hash",
            version_id="hash",
            expected_current=baseline,
            policy_publication=replace(pending, snapshot_sha256="0" * 64),
        ).visibility
        == "NOT_COMMITTED"
    )
    assert (
        store.publish(
            content(1),
            publication_id="changed",
            version_id="v1",
            expected_current=baseline,
            policy_publication=replace(
                pending, snapshot_sha256=calendar_snapshot_digest(content(1))
            ),
        ).visibility
        == "NOT_COMMITTED"
    )
    assert (
        store.publish(
            content(),
            publication_id="republish",
            version_id="v0",
            republish=True,
            expected_current=baseline,
            policy_publication=pending,
        ).visibility
        == "NOT_COMMITTED"
    )
    assert {
        p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    } == before
    revised = switch(store, policy.revision, "manual").policy
    assert revised.revision != policy.revision
    assert (
        store.publish(
            content(),
            publication_id="policy-stale",
            version_id="policy-stale",
            expected_current=baseline,
            policy_publication=pending,
        ).visibility
        == "NOT_COMMITTED"
    )


@pytest.mark.parametrize("covered", [False, True])
def test_historical_v2_and_v3_pending_recovery_and_original_id_replay(tmp_path, covered):
    store = TradingCalendarStore(tmp_path)
    artifacts = {}
    if covered:
        digest = module._sha(module._content(content()))
        manifest = module._canonical(
            {
                "schema": module._MANIFEST,
                "snapshot_sha256": digest,
                "coverage": [
                    {
                        "venue": "SHFE",
                        "coverage_start": "2026-01-01",
                        "coverage_end": "2026-12-31",
                        "semantics": "DECLARED_INCLUSIVE",
                    }
                ],
            }
        )
        manifest_sha = module._sha(manifest)
        binding = module._canonical(
            {"schema": module._BINDING, "snapshot_sha256": digest, "manifest_sha256": manifest_sha}
        )
        artifacts = {
            "coverage_manifest_bytes": manifest,
            "coverage_manifest_sha256": manifest_sha,
            "binding_bytes": binding,
            "binding_sha256": module._sha(binding),
        }
    snapshot = module._snapshot_bytes(
        module._content(content()),
        *(
            artifacts.get(key)
            for key in (
                "coverage_manifest_bytes",
                "coverage_manifest_sha256",
                "binding_bytes",
                "binding_sha256",
            )
        ),
    )
    event = {
        "schema": module._EVENT_COVERED if covered else module._EVENT_TIMED,
        "publication_id": "old",
        "sequence": 1,
        "previous": None,
        "version_id": "old",
        "content_sha256": module._sha(module._content(content())),
        "lower_utc": None,
        "monotonic_ns": None,
        "boot_id": None,
        "utc_bound_us": None,
    }
    if covered:
        event["version_sha256"] = module._sha(snapshot)
    event_bytes = module._canonical(event)
    pointer = {
        "schema": module._POINTER,
        "publication_id": "old",
        "publication_sha256": module._sha(event_bytes),
    }
    intent = {
        "schema": module._INTENT,
        "publication_id": "old",
        "version_id": "old",
        "pointer": pointer,
    }
    with store._writer_lock():
        store._write_new(tmp_path / "versions/old.json", snapshot)
        store._write_new(tmp_path / "publications/old.json", event_bytes)
        store._write_new(tmp_path / "CURRENT.json", module._canonical(pointer))
        store._write_new(tmp_path / "PENDING.json", module._canonical(intent))
    assert store.read_current().version_id == "old"
    assert store.verify_publication("old") == "UNKNOWN"
    with pytest.raises(CalendarStoreError):
        store.read_current_with_consistent_receipts()
    assert store.reconcile_publication("old").visibility == "COMMITTED"
    assert store.read_current_with_consistent_receipts().snapshot.version_id == "old"
    assert (
        store.publish(content(), publication_id="old", version_id="old", **artifacts).visibility
        == "COMMITTED"
    )
    assert store.read_publication_policy("old") is None
    assert not (tmp_path / "PENDING.json").exists()


def test_default_only_after_explicit_successful_config_and_legacy_compatibility(tmp_path):
    store = TradingCalendarStore(tmp_path)
    seed_legacy(store)
    assert store.read_current().version_id == "old"
    assert (
        store.publish(content(), publication_id="old", version_id="old").visibility == "COMMITTED"
    )
    policy = store.initialize_review_policy(config_read_succeeded=True)
    assert (policy.mode, policy.mode_source) == ("auto", "default")
    assert (
        store.publish(content(1), publication_id="p1", version_id="v1").visibility
        == "NOT_COMMITTED"
    )
    assert store.read_current().publication_id == "old"
    assert store.read_publication_policy("old") is None
    with pytest.raises(CalendarStoreError):
        store.initialize_review_policy(config_read_succeeded=True)


@pytest.mark.parametrize("damage", ["missing_policy", "missing_marker", "corrupt", "bad_mode"])
def test_broken_initialized_policy_never_defaults_or_publishes(tmp_path, damage):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    marker, path = tmp_path / "REVIEW_POLICY_INITIALIZED.json", tmp_path / "REVIEW_POLICY.json"
    if damage == "missing_policy":
        path.unlink()
    elif damage == "missing_marker":
        marker.unlink()
    elif damage == "corrupt":
        path.write_bytes(b"{}")
    else:
        value = json.loads(path.read_bytes())
        value["mode"] = "bad"
        path.write_bytes(module._canonical(value))
    assert publish(store, policy).visibility == "NOT_COMMITTED"
    assert (
        store.publish(content(), publication_id="unguarded", version_id="unguarded").visibility
        == "NOT_COMMITTED"
    )
    assert artifact_paths(tmp_path) == []
    with pytest.raises(CalendarStoreError):
        store.read_review_policy()


@pytest.mark.parametrize(
    "change",
    [
        {"snapshot_sha256": "0" * 64},
        {"difference_sha256": "0" * 64},
        {"reviewer": None},
        {"confirmation_snapshot_sha256": "0" * 64},
        {"confirmation_difference_sha256": "0" * 64},
        {"directory_id": "other"},
        {"policy_revision": "stale"},
        {"mode": "auto"},
        {"confirmation_policy_revision": "stale"},
        {"confirmation_current": CurrentPrecondition.matching("other", "0" * 64)},
        {"confirmation_utc": None},
        {"confirmation_utc": "2026-01-01T00:00:00Z"},
    ],
)
def test_manual_claims_and_stale_policy_are_rejected_before_artifacts(tmp_path, change):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    assert (
        publish(store, policy, evidence=replace(claim(policy), **change)).visibility
        == "NOT_COMMITTED"
    )
    assert artifact_paths(tmp_path) == []


def test_exact_evidence_record_current_cas_and_id_replay(tmp_path):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    first = claim(policy)
    assert publish(store, policy, evidence=first).visibility == "COMMITTED"
    event = json.loads((tmp_path / "publications/p0.json").read_bytes())
    evidence = event["review_policy"]
    assert (event["schema"], evidence["identity_status"], evidence["source_status"]) == (
        module._EVENT_POLICY,
        "UNAUTHENTICATED",
        "UNVERIFIED",
    )
    assert evidence["difference"] == {"changed": 0} and evidence["reviewer"] == "reviewer"
    assert evidence["confirmation_utc"] == "2026-01-01T00:00:00.000000Z"
    assert store.read_publication_policy("p0") == evidence
    assert publish(store, policy, evidence=first).visibility == "COMMITTED"
    assert (
        publish(store, policy, evidence=claim(policy, difference={"other": 1})).visibility
        == "ID_CONFLICT"
    )
    before = artifact_paths(tmp_path)
    assert publish(store, policy, n=1).visibility == "NOT_COMMITTED"
    assert artifact_paths(tmp_path) == before
    current = store.read_current()
    expected = CurrentPrecondition.matching(current.publication_id, current.publication_sha256)
    assert publish(store, policy, n=1, expected=expected).visibility == "COMMITTED"


def test_replay_of_proven_id_survives_later_policy_change(tmp_path):
    store = TradingCalendarStore(tmp_path)
    manual = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    old_claim = claim(manual)
    assert publish(store, manual, evidence=old_claim).visibility == "COMMITTED"
    auto = switch(store, manual.revision, "auto").policy
    assert store.reconcile_publication("p0").visibility == "COMMITTED"
    assert publish(store, manual, evidence=old_claim).visibility == "COMMITTED"
    assert (
        publish(store, manual, evidence=replace(old_claim, reviewer="other")).visibility
        == "ID_CONFLICT"
    )
    assert publish(store, manual, n=1).visibility == "NOT_COMMITTED"
    assert store.read_review_policy() == auto


def test_deleted_both_policy_files_do_not_bypass_recorded_policy_history(tmp_path):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    assert publish(store, policy).visibility == "COMMITTED"
    (tmp_path / "REVIEW_POLICY_INITIALIZED.json").unlink()
    (tmp_path / "REVIEW_POLICY.json").unlink()
    result = store.publish(content(1), publication_id="other", version_id="other")
    assert result.visibility == "NOT_COMMITTED"
    assert store.read_current().publication_id == "p0"
    with pytest.raises(CalendarStoreError):
        store.initialize_review_policy(config_read_succeeded=True)


def test_deleted_policy_files_before_first_publication_do_not_enable_unguarded_write(tmp_path):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    (tmp_path / "REVIEW_POLICY_INITIALIZED.json").unlink()
    (tmp_path / "REVIEW_POLICY.json").unlink()
    assert (
        store.publish(content(), publication_id="p0", version_id="v0").visibility == "NOT_COMMITTED"
    )
    assert publish(store, policy).visibility == "NOT_COMMITTED"
    with pytest.raises(CalendarStoreError):
        store.initialize_review_policy(config_read_succeeded=True)
    assert not (tmp_path / "CURRENT.json").exists()
    assert artifact_paths(tmp_path) == []


def test_partial_lock_marker_never_defaults_or_reinitializes(tmp_path):
    store = TradingCalendarStore(tmp_path)
    with store._writer_lock():
        (tmp_path / ".lock").write_bytes(b"{")
    with pytest.raises(CalendarStoreError):
        store.initialize_review_policy(config_read_succeeded=True)
    assert (
        store.publish(content(), publication_id="p0", version_id="v0").visibility == "NOT_COMMITTED"
    )
    assert not (tmp_path / "CURRENT.json").exists()


@pytest.mark.parametrize("replace_done", [False, True])
def test_policy_switch_replace_failure_is_unknown_with_observed_revision(
    tmp_path, monkeypatch, replace_done
):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    real_replace = module.os.replace

    def fail(src, dst):
        if replace_done:
            real_replace(src, dst)
        raise OSError("injected replace failure")

    monkeypatch.setattr(module.os, "replace", fail)
    result = switch(store, policy.revision, "auto", operation_id="interrupted")
    assert result.status == "COMMIT_UNKNOWN"
    with pytest.raises(CalendarStoreError):
        store.read_review_policy()
    assert (tmp_path / "POLICY_CHANGE_PENDING.json").exists()
    monkeypatch.setattr(module.os, "replace", real_replace)
    recovered = store.reconcile_review_policy_change("interrupted")
    assert recovered.status == "COMMITTED"
    assert recovered.policy.mode == "auto"
    assert store.read_review_policy() == recovered.policy
    assert not (tmp_path / "CURRENT.json").exists()


def test_tampered_review_evidence_fails_chain_validation(tmp_path):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    assert publish(store, policy).visibility == "COMMITTED"
    path = tmp_path / "publications/p0.json"
    event = json.loads(path.read_bytes())
    event["review_policy"]["difference"] = {"changed": 99}
    event_bytes = module._canonical(event)
    path.write_bytes(event_bytes)
    pointer_path = tmp_path / "CURRENT.json"
    pointer = json.loads(pointer_path.read_bytes())
    pointer["publication_sha256"] = hashlib.sha256(event_bytes).hexdigest()
    pointer_path.write_bytes(module._canonical(pointer))
    with pytest.raises(CalendarStoreError):
        store.read_current()
    with pytest.raises(CalendarStoreError):
        store.read_publication_policy("p0")


def test_uncertain_commit_recovers_same_id_and_bound_evidence(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    original_replace = module.os.replace

    def uncertain(src, dst):
        original_replace(src, dst)
        if str(dst).endswith("CURRENT.json"):
            raise OSError("after replace")

    monkeypatch.setattr(module.os, "replace", uncertain)
    assert publish(store, policy).visibility == "COMMIT_UNKNOWN"
    assert store.reconcile_publication("p0").visibility == "COMMITTED"
    assert store.read_current().publication_id == "p0"
    assert (
        json.loads((tmp_path / "publications/p0.json").read_bytes())["review_policy"]["reviewer"]
        == "reviewer"
    )


def test_covered_snapshot_and_declared_evidence_round_trip(tmp_path):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True)
    content_sha = hashlib.sha256(module._content(content())).hexdigest()
    manifest = module._canonical(
        {
            "schema": module._MANIFEST,
            "snapshot_sha256": content_sha,
            "coverage": [
                {
                    "venue": "SHFE",
                    "coverage_start": "2026-01-01",
                    "coverage_end": "2026-12-31",
                    "semantics": "DECLARED_INCLUSIVE",
                }
            ],
        }
    )
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    binding = module._canonical(
        {"schema": module._BINDING, "snapshot_sha256": content_sha, "manifest_sha256": manifest_sha}
    )
    args = {
        "coverage_manifest_bytes": manifest,
        "coverage_manifest_sha256": manifest_sha,
        "binding_bytes": binding,
        "binding_sha256": hashlib.sha256(binding).hexdigest(),
    }
    evidence = replace(claim(policy), snapshot_sha256=calendar_snapshot_digest(content(), **args))
    assert (
        store.publish(
            content(),
            publication_id="covered",
            version_id="covered",
            expected_current=CurrentPrecondition.absent(),
            policy_publication=evidence,
            **args,
        ).visibility
        == "COMMITTED"
    )
    current = store.read_current()
    assert store.read_current_with_consistent_receipts().snapshot == current
    assert current.coverage_status == "DECLARED_UNVERIFIED"
    assert current.coverage_manifest_bytes == manifest
    event = json.loads((tmp_path / "publications/covered.json").read_bytes())
    assert event["schema"] == module._EVENT_POLICY_COVERED
    assert event["review_policy"]["snapshot_sha256"] == evidence.snapshot_sha256
    assert store.reconcile_publication("covered").visibility == "COMMITTED"


def test_manual_noop_compares_complete_covered_snapshot_bytes(tmp_path):
    def bundle(end):
        content_sha = module._sha(module._content(content()))
        manifest = module._canonical(
            {
                "schema": module._MANIFEST,
                "snapshot_sha256": content_sha,
                "coverage": [
                    {
                        "venue": "SHFE",
                        "coverage_start": "2026-01-01",
                        "coverage_end": end,
                        "semantics": "DECLARED_INCLUSIVE",
                    }
                ],
            }
        )
        manifest_sha = module._sha(manifest)
        binding = module._canonical(
            {
                "schema": module._BINDING,
                "snapshot_sha256": content_sha,
                "manifest_sha256": manifest_sha,
            }
        )
        return {
            "coverage_manifest_bytes": manifest,
            "coverage_manifest_sha256": manifest_sha,
            "binding_bytes": binding,
            "binding_sha256": module._sha(binding),
        }

    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    first_bundle = bundle("2026-12-31")
    first_hash = calendar_snapshot_digest(content(), **first_bundle)
    first_claim = replace(
        claim(policy), snapshot_sha256=first_hash, confirmation_snapshot_sha256=first_hash
    )
    assert (
        store.publish(
            content(),
            publication_id="p0",
            version_id="v0",
            **first_bundle,
            expected_current=CurrentPrecondition.absent(),
            policy_publication=first_claim,
        ).visibility
        == "COMMITTED"
    )
    current = store.read_current()
    baseline = CurrentPrecondition.matching(current.publication_id, current.publication_sha256)
    pending = replace(
        claim(policy, expected=baseline),
        snapshot_sha256=first_hash,
        reviewer=None,
        confirmation_snapshot_sha256=None,
        confirmation_difference_sha256=None,
        confirmation_policy_revision=None,
        confirmation_current=None,
        confirmation_utc=None,
    )
    before = artifact_paths(tmp_path)
    result = store.publish(
        content(),
        publication_id="noop",
        version_id="noop",
        **first_bundle,
        expected_current=baseline,
        policy_publication=pending,
    )
    assert result.visibility == "UNCHANGED"
    assert "not proven" in result.detail
    second_bundle = bundle("2026-11-30")
    changed = replace(pending, snapshot_sha256=calendar_snapshot_digest(content(), **second_bundle))
    assert (
        store.publish(
            content(),
            publication_id="changed",
            version_id="changed",
            **second_bundle,
            expected_current=baseline,
            policy_publication=changed,
        ).visibility
        == "NOT_COMMITTED"
    )
    assert artifact_paths(tmp_path) == before


def test_policy_event_history_and_historical_review_retrieval(tmp_path, monkeypatch):
    class ManualClock:
        def __init__(self):
            self.value = datetime(2026, 1, 1, tzinfo=UTC)
            self.counter = 1_000_000_000_000

        def wall(self):
            return self.value

        def mono(self):
            return self.counter

        def advance(self, seconds):
            self.value += timedelta(seconds=seconds)
            self.counter += seconds * 1_000_000_000

    monkeypatch.setattr(
        module, "_system_boot_identity", lambda: "linux:00000000-0000-0000-0000-000000000001"
    )
    clock = ManualClock()
    store = TradingCalendarStore(
        tmp_path,
        clock_utc=clock.wall,
        monotonic_ns=clock.mono,
        utc_error_bound=timedelta(seconds=1),
    )
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    start = clock.value
    assert publish(store, policy).visibility == "COMMITTED"
    clock.advance(10)
    first = store.read_current()
    baseline = CurrentPrecondition.matching(first.publication_id, first.publication_sha256)
    assert publish(store, policy, n=1, expected=baseline).visibility == "COMMITTED"
    clock.advance(10)
    assert store.history_at(start + timedelta(seconds=5)).snapshot.version_id == "v0"
    assert store.history_at(start + timedelta(seconds=15)).snapshot.version_id == "v1"
    assert store.read_publication_policy("p0")["reviewer"] == "reviewer"


def _hold_writer(root, ready, release):
    store = TradingCalendarStore(root)
    with store._writer_lock():
        ready.set()
        release.wait(10)


def _racing_action(root, policy, start, results, action):
    store = TradingCalendarStore(root)
    start.wait(10)
    try:
        if action == "switch":
            results.put(
                (
                    action,
                    switch(store, policy.revision, "auto").status,
                )
            )
        else:
            results.put((action, publish(store, policy).visibility))
    except (OSError, CalendarStoreError):
        results.put((action, "LOCK_OR_POLICY_REJECTED"))


def test_separate_process_policy_change_and_publication_share_writer_lock(tmp_path):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    proc = ctx.Process(target=_hold_writer, args=(tmp_path, ready, release))
    proc.start()
    try:
        assert ready.wait(10)
        assert switch(store, policy.revision, "auto").status == "COMMIT_UNKNOWN"
        assert publish(store, policy).visibility != "COMMITTED"
        assert not (tmp_path / "CURRENT.json").exists()
    finally:
        release.set()
        proc.join(10)
    assert proc.exitcode == 0
    new = switch(store, policy.revision, "auto").policy
    assert publish(store, policy).visibility == "NOT_COMMITTED"
    assert publish(store, new).visibility == "COMMITTED"


def test_concurrent_switch_and_actual_manual_publication_are_serialized(tmp_path):
    store = TradingCalendarStore(tmp_path)
    policy = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    ctx = multiprocessing.get_context("spawn")
    start, results = ctx.Event(), ctx.Queue()
    processes = [
        ctx.Process(target=_racing_action, args=(tmp_path, policy, start, results, action))
        for action in ("switch", "publish")
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(15)
    assert all(process.exitcode == 0 for process in processes)
    outcomes = dict(results.get(timeout=5) for _ in processes)
    if outcomes["publish"] == "COMMITTED":
        event = json.loads((tmp_path / "publications/p0.json").read_bytes())
        assert event["review_policy"]["mode"] == "manual"
        assert event["review_policy"]["policy_revision"] == policy.revision
    else:
        assert not (tmp_path / "CURRENT.json").exists()
    if outcomes["switch"] == "COMMITTED":
        assert store.read_review_policy().mode == "auto"


def test_audit_chain_original_id_replay_and_id_conflict(tmp_path):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    first = switch(store, old.revision, "auto", operation_id="change1")
    assert first.status == "COMMITTED" and first.policy.mode == "auto"
    record = store.read_policy_change("change1")
    assert (record["old_policy"]["revision"], record["old_policy"]["mode"], record["new_mode"]) == (
        old.revision,
        "manual",
        "auto",
    )
    assert (
        record["actor"],
        record["at_utc"],
        record["identity_status"],
        record["clock_status"],
        record["prior_history"],
    ) == ("operator1", "2026-01-01T00:00:00.000000Z", "UNAUTHENTICATED", "UNVERIFIED", "UNKNOWN")
    assert record["directory_id"] == old.directory_id
    assert not (tmp_path / "CURRENT.json").exists()
    assert switch(store, old.revision, "auto", operation_id="change1") == first
    for changed in (
        {"actor": "someone_else"},
        {"at_utc": "2026-01-02T00:00:00.000000Z"},
        {"mode": "manual"},
        {"expected_revision": "other"},
    ):
        kwargs = {
            "expected_revision": old.revision,
            "mode": "auto",
            "operation_id": "change1",
            "actor": "operator1",
            "at_utc": "2026-01-01T00:00:00.000000Z",
            **changed,
        }
        assert store.change_review_policy(**kwargs).status == "ID_CONFLICT"
    second = switch(store, first.policy.revision, "manual", operation_id="change2")
    assert second.status == "COMMITTED"
    assert store.read_policy_change("change2")["prior_history"] == "CHAINED"
    assert (
        switch(store, old.revision, "auto", operation_id="change1").status == "HISTORICAL_COMMITTED"
    )
    assert store.reconcile_review_policy_change("change1").status == "HISTORICAL_COMMITTED"
    assert store.reconcile_review_policy_change("change2").status == "COMMITTED"
    assert not (tmp_path / "CURRENT.json").exists()


@pytest.mark.parametrize(
    "actor,at_utc",
    [
        ("", "2026-01-01T00:00:00.000000Z"),
        ("anonymous", "2026-01-01T00:00:00.000000Z"),
        ("ANON", "2026-01-01T00:00:00.000000Z"),
        ("unknown", "2026-01-01T00:00:00.000000Z"),
        ("user/invalid", "2026-01-01T00:00:00.000000Z"),
        ("operator1", "2026-01-01T00:00:00Z"),
        ("operator1", "2026-01-01T00:00:00.000000+08:00"),
        ("operator1", None),
    ],
)
def test_audit_requires_nonanonymous_actor_and_canonical_utc(tmp_path, actor, at_utc):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    result = store.change_review_policy(
        expected_revision=old.revision, mode="auto", operation_id="id1", actor=actor, at_utc=at_utc
    )
    assert result.status == "NOT_COMMITTED"
    assert store.read_review_policy() == old
    assert not (tmp_path / "POLICY_CHANGE_PENDING.json").exists()


@pytest.mark.parametrize("stage", ["before_switch", "after_switch", "after_record"])
def test_interrupted_policy_change_blocks_then_recovers_original_intent(
    tmp_path, monkeypatch, stage
):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    initial_write, initial_unlink = store._write_new, Path.unlink

    def write_fault(path, data):
        if (stage == "before_switch" and path.name.startswith(".REVIEW_POLICY-")) or (
            stage == "after_switch" and path.parent.name == "policy_changes"
        ):
            raise OSError("injected")
        return initial_write(path, data)

    def unlink_fault(path, *args, **kwargs):
        if stage == "after_record" and path.name == "POLICY_CHANGE_PENDING.json":
            raise OSError("injected")
        return initial_unlink(path, *args, **kwargs)

    monkeypatch.setattr(store, "_write_new", write_fault)
    monkeypatch.setattr(Path, "unlink", unlink_fault)
    result = switch(store, old.revision, "auto", operation_id="interrupted")
    assert result.status == "COMMIT_UNKNOWN"
    assert (tmp_path / "POLICY_CHANGE_PENDING.json").exists()
    assert publish(store, old).visibility == "COMMIT_UNKNOWN"
    assert (
        switch(store, old.revision, "manual", operation_id="different").status == "COMMIT_UNKNOWN"
    )
    assert store.reconcile_review_policy_change("different").status == "COMMIT_UNKNOWN"
    assert switch(store, old.revision, "manual", operation_id="interrupted").status == "ID_CONFLICT"
    assert (
        switch(store, old.revision, "auto", operation_id="interrupted").status == "COMMIT_UNKNOWN"
    )
    with pytest.raises(CalendarStoreError):
        store.read_review_policy()
    monkeypatch.setattr(store, "_write_new", initial_write)
    monkeypatch.setattr(Path, "unlink", initial_unlink)
    recovered = store.reconcile_review_policy_change("interrupted")
    assert recovered.status == "COMMITTED" and recovered.policy.mode == "auto"
    assert store.read_policy_change("interrupted")["new_revision"] == recovered.policy.revision
    assert not (tmp_path / "POLICY_CHANGE_PENDING.json").exists()
    assert not (tmp_path / "CURRENT.json").exists()


def test_partial_intent_or_tampered_record_fails_closed(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    write = store._write_new

    def partial(path, data):
        if path.name == "POLICY_CHANGE_PENDING.json":
            write(path, b"{")
            raise OSError("partial intent")
        return write(path, data)

    monkeypatch.setattr(store, "_write_new", partial)
    assert switch(store, old.revision, "auto", "partial").status == "COMMIT_UNKNOWN"
    monkeypatch.setattr(store, "_write_new", write)
    assert store.reconcile_review_policy_change("partial").status == "COMMIT_UNKNOWN"
    assert publish(store, old).visibility == "COMMIT_UNKNOWN"
    assert (tmp_path / "POLICY_CHANGE_PENDING.json").read_bytes() == b"{"


def test_committed_audit_record_tampering_rejects_reads_and_publications(tmp_path):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    change = switch(store, old.revision, "auto", "tamper")
    assert change.status == "COMMITTED"
    path = tmp_path / "policy_changes/tamper.json"
    event = json.loads(path.read_bytes())
    event["actor"] = "other"
    path.write_bytes(module._canonical(event))
    with pytest.raises(CalendarStoreError):
        store.read_review_policy()
    assert store.reconcile_review_policy_change("tamper").status == "COMMIT_UNKNOWN"
    assert publish(store, change.policy).visibility != "COMMITTED"


def test_corrupt_record_after_switch_cannot_be_reconciled(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    write = store._write_new

    def write_then_fail(path, data):
        write(path, data)
        if path.parent.name == "policy_changes":
            raise OSError("uncertain after write")

    monkeypatch.setattr(store, "_write_new", write_then_fail)
    assert switch(store, old.revision, "auto", "damaged").status == "COMMIT_UNKNOWN"
    monkeypatch.setattr(store, "_write_new", write)
    path = tmp_path / "policy_changes/damaged.json"
    path.write_bytes(b"{}")
    assert store.reconcile_review_policy_change("damaged").status == "COMMIT_UNKNOWN"
    assert publish(store, old).visibility == "COMMIT_UNKNOWN"
    assert (tmp_path / "POLICY_CHANGE_PENDING.json").exists()


@pytest.mark.parametrize("damage", ["policy_downgrade", "marker_removed", "marker_corrupt"])
def test_audit_history_cannot_be_silently_downgraded(tmp_path, damage):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    changed = switch(store, old.revision, "auto", "first")
    assert changed.status == "COMMITTED"
    if damage == "policy_downgrade":
        old_data = store.read_policy_change("first")["old_policy"]
        (tmp_path / "REVIEW_POLICY.json").write_bytes(module._canonical(old_data))
    elif damage == "marker_removed":
        (tmp_path / "POLICY_AUDIT_INITIALIZED.json").unlink()
    else:
        (tmp_path / "POLICY_AUDIT_INITIALIZED.json").write_bytes(b"{}")
    with pytest.raises((CalendarStoreError, OSError)):
        store.read_review_policy()
    assert store.reconcile_review_policy_change("first").status == "COMMIT_UNKNOWN"
    assert publish(store, changed.policy).visibility != "COMMITTED"


def test_upgrade_marker_written_before_switch_can_resume_original_id(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    original_replace = module.os.replace

    def crash_before_switch(src, dst):
        raise OSError("switch unavailable")

    monkeypatch.setattr(module.os, "replace", crash_before_switch)
    assert switch(store, old.revision, "auto", "upgrade").status == "COMMIT_UNKNOWN"
    assert (tmp_path / "POLICY_AUDIT_INITIALIZED.json").exists()
    assert json.loads((tmp_path / "REVIEW_POLICY.json").read_bytes())["schema"] == module._POLICY
    assert (tmp_path / "POLICY_CHANGE_PENDING.json").exists()
    monkeypatch.setattr(module.os, "replace", original_replace)
    result = store.reconcile_review_policy_change("upgrade")
    assert result.status == "COMMITTED" and result.policy.mode == "auto"
    assert store.read_policy_change("upgrade")["prior_history"] == "UNKNOWN"


def test_partial_upgrade_marker_blocks_and_does_not_overwrite_evidence(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    original_write = store._write_new

    def fail_marker(path, data):
        if path.name == "POLICY_AUDIT_INITIALIZED.json":
            original_write(path, b"{")
            raise OSError("partial marker")
        original_write(path, data)

    monkeypatch.setattr(store, "_write_new", fail_marker)
    assert switch(store, old.revision, "auto", "upgrade").status == "COMMIT_UNKNOWN"
    monkeypatch.setattr(store, "_write_new", original_write)
    assert store.reconcile_review_policy_change("upgrade").status == "COMMIT_UNKNOWN"
    assert (tmp_path / "POLICY_AUDIT_INITIALIZED.json").read_bytes() == b"{"
    assert (tmp_path / "POLICY_CHANGE_PENDING.json").exists()
    assert publish(store, old).visibility == "COMMIT_UNKNOWN"


def _crash_during_policy_change(root, revision, stage):
    import os

    store = TradingCalendarStore(root)
    write, replace_path = store._write_new, module.os.replace

    def interrupted_write(path, data):
        write(path, data)
        if (stage == "intent" and path.name == "POLICY_CHANGE_PENDING.json") or (
            stage == "record" and path.parent.name == "policy_changes"
        ):
            os._exit(73)

    def interrupted_replace(src, dst):
        replace_path(src, dst)
        if stage == "replacement":
            os._exit(73)

    store._write_new = interrupted_write
    module.os.replace = interrupted_replace
    switch(store, revision, "auto", operation_id="crash")
    os._exit(74)


@pytest.mark.parametrize("stage", ["intent", "replacement", "record"])
def test_policy_change_process_crash_recovery_is_bound_to_original_id(tmp_path, stage):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=_crash_during_policy_change, args=(tmp_path, old.revision, stage))
    proc.start()
    proc.join(15)
    assert proc.exitcode == 73
    assert (tmp_path / "POLICY_CHANGE_PENDING.json").exists()
    assert publish(store, old).visibility == "COMMIT_UNKNOWN"
    assert store.reconcile_review_policy_change("wrong").status == "COMMIT_UNKNOWN"
    recovered = store.reconcile_review_policy_change("crash")
    assert recovered.status == "COMMITTED"
    assert store.read_policy_change("crash")["new_revision"] == recovered.policy.revision
    assert not (tmp_path / "POLICY_CHANGE_PENDING.json").exists()
    assert not (tmp_path / "CURRENT.json").exists()


def test_old_valid_policy_cannot_hide_later_audited_record(tmp_path):
    store = TradingCalendarStore(tmp_path)
    initial = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    first = switch(store, initial.revision, "auto", "a")
    old_policy_bytes = (tmp_path / "REVIEW_POLICY.json").read_bytes()
    second = switch(store, first.policy.revision, "manual", "b")
    assert second.status == "COMMITTED"
    (tmp_path / "REVIEW_POLICY.json").write_bytes(old_policy_bytes)
    with pytest.raises(CalendarStoreError):
        store.read_review_policy()
    assert publish(store, first.policy).visibility != "COMMITTED"
    assert switch(store, first.policy.revision, "auto", "c").status == "COMMIT_UNKNOWN"
    assert (tmp_path / "policy_changes/b.json").exists()


def test_legacy_downgrade_and_removed_audit_marker_still_detects_records(tmp_path):
    store = TradingCalendarStore(tmp_path)
    initial = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    legacy_bytes = (tmp_path / "REVIEW_POLICY.json").read_bytes()
    assert switch(store, initial.revision, "auto", "a").status == "COMMITTED"
    (tmp_path / "REVIEW_POLICY.json").write_bytes(legacy_bytes)
    (tmp_path / "POLICY_AUDIT_INITIALIZED.json").unlink()
    with pytest.raises(CalendarStoreError):
        store.read_review_policy()
    assert publish(store, initial).visibility != "COMMITTED"


@pytest.mark.parametrize("entry", ["orphan", "bad_name", "symlink", "directory", "oversized"])
def test_unrecognized_or_unlinked_policy_directory_entries_fail_closed(tmp_path, entry):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    changed = switch(store, old.revision, "auto", "a")
    assert changed.status == "COMMITTED"
    directory = tmp_path / "policy_changes"
    if entry == "orphan":
        (directory / "extra.json").write_bytes((directory / "a.json").read_bytes())
    elif entry == "bad_name":
        (directory / "unexpected.txt").write_bytes(b"x")
    elif entry == "symlink":
        (directory / "extra.json").symlink_to(directory / "a.json")
    elif entry == "directory":
        (directory / "extra.json").mkdir()
    else:
        (directory / "a.json").write_bytes(b"x" * (module._POLICY_MAX_RECORD_BYTES + 1))
    with pytest.raises((CalendarStoreError, OSError)):
        store.read_review_policy()
    assert publish(store, changed.policy).visibility != "COMMITTED"


def test_policy_record_count_and_total_byte_limits_fail_closed(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    initial = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    first = switch(store, initial.revision, "auto", "a")
    assert first.status == "COMMITTED"
    assert switch(store, first.policy.revision, "manual", "b").status == "COMMITTED"
    with monkeypatch.context() as patch:
        patch.setattr(module, "_POLICY_MAX_RECORDS", 1)
        with pytest.raises(CalendarStoreError):
            store.read_review_policy()
    with monkeypatch.context() as patch:
        patch.setattr(module, "_POLICY_MAX_TOTAL_BYTES", 1)
        with pytest.raises(CalendarStoreError):
            store.read_review_policy()


def test_pending_recovery_rejects_extra_record_but_accepts_original_record(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    write = store._write_new

    def write_record_then_fail(path, data):
        write(path, data)
        if path.parent.name == "policy_changes":
            raise OSError("injected")

    monkeypatch.setattr(store, "_write_new", write_record_then_fail)
    assert switch(store, old.revision, "auto", "original").status == "COMMIT_UNKNOWN"
    monkeypatch.setattr(store, "_write_new", write)
    directory = tmp_path / "policy_changes"
    extra = directory / "extra.json"
    extra.write_bytes((directory / "original.json").read_bytes())
    assert store.reconcile_review_policy_change("original").status == "COMMIT_UNKNOWN"
    assert (tmp_path / "POLICY_CHANGE_PENDING.json").exists()
    extra.unlink()
    assert store.reconcile_review_policy_change("original").status == "COMMITTED"


def test_failure_before_intent_reports_proven_not_committed_under_lock(tmp_path, monkeypatch):
    store = TradingCalendarStore(tmp_path)
    old = store.initialize_review_policy(config_read_succeeded=True, mode="manual")
    directory = store._directory

    def fail_before_intent(path, *, create=False):
        if path.name == "policy_changes" and create:
            raise OSError("injected before intent")
        return directory(path, create=create)

    monkeypatch.setattr(store, "_directory", fail_before_intent)
    result = switch(store, old.revision, "auto", "before")
    assert result.status == "NOT_COMMITTED" and result.detail == "intent was not created"
    assert not (tmp_path / "POLICY_CHANGE_PENDING.json").exists()
    assert store.read_review_policy() == old
