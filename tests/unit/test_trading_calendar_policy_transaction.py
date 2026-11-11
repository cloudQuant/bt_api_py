"""Cooperating local writers only; no exchange or identity is authenticated."""

import hashlib
import json
import multiprocessing
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from bt_api_py import trading_calendar_store as module
from bt_api_py.trading_calendar_store import (
    CalendarPolicyChangeUnknownError,
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
    auto = store.change_review_policy(expected_revision=policy.revision, mode="auto")
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
    revised = store.change_review_policy(expected_revision=policy.revision, mode="manual")
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
    assert store.reconcile_publication("old").visibility == "COMMITTED"
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
    auto = store.change_review_policy(expected_revision=manual.revision, mode="auto")
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
    with pytest.raises(CalendarPolicyChangeUnknownError) as caught:
        store.change_review_policy(expected_revision=policy.revision, mode="auto")
    observed = store.read_review_policy()
    assert caught.value.observed_policy == observed
    assert (observed.mode == "auto") is replace_done
    assert (observed.revision != policy.revision) is replace_done
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
                    store.change_review_policy(expected_revision=policy.revision, mode="auto").mode,
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
        with pytest.raises((OSError, CalendarStoreError)):
            store.change_review_policy(expected_revision=policy.revision, mode="auto")
        assert publish(store, policy).visibility != "COMMITTED"
        assert not (tmp_path / "CURRENT.json").exists()
    finally:
        release.set()
        proc.join(10)
    assert proc.exitcode == 0
    new = store.change_review_policy(expected_revision=policy.revision, mode="auto")
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
    if outcomes["switch"] == "auto":
        assert store.read_review_policy().mode == "auto"
