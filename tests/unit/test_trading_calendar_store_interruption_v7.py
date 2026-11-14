"""Offline /7 reader fixtures; these tests do not implement a publication path."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot as CtpSnapshot

from bt_api_py import trading_calendar_store as module
from bt_api_py.trading_calendar_binding import SimulationCalendarBinding
from bt_api_py.trading_calendar_interruption_compare_simulation import (
    compare_simulated_interruption,
)
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_binding import FakeGate
from tests.unit.test_trading_calendar_interruption_view_simulation import FIRST, fixture, publish


def _declaration(snapshot):
    return {
        "content": snapshot.content,
        "coverage": json.loads(snapshot.coverage_manifest_bytes)["coverage"],
        "data_origin": "MOCK",
        "simulation_provenance": snapshot.simulation_provenance,
    }


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _store_v7(root, clock=None):
    store = (
        FixtureCalendarStore(root)
        if clock is None
        else FixtureCalendarStore(
            root, clock_utc=clock.wall, monotonic_ns=clock.mono, utc_error_bound=module.timedelta(0)
        )
    )
    first = fixture()
    publish(store, first, 1)
    before = store.read_current()
    if clock is not None:
        clock.advance(5)
    later = fixture(end="2026-06-02T02:30:00.000003Z")
    publish(store, later, 2)
    old_after = store.read_current()
    if clock is not None:
        clock.advance(5)
    diff = compare_simulated_interruption(
        _declaration(before),
        _declaration(old_after),
        mode="SIMULATION_ONLY",
        snapshot_factory=CtpSnapshot,
    )
    assert diff["status"] == "CHANGED"
    version_path = root / "versions" / "v2.json"
    version = json.loads(version_path.read_bytes())
    version["schema"] = "bt_api.calendar-store.snapshot/7"
    version_bytes = module._canonical(version)
    version_path.write_bytes(version_bytes)
    event_path = root / "publications" / "pub-v2.json"
    event = json.loads(event_path.read_bytes())
    event["schema"] = "bt_api.calendar-store.publication/7"
    event["version_sha256"] = _sha(version_bytes)
    difference = {
        "schema": "bt_api.calendar-interruption-publication-difference/1",
        "candidate_id": "a" * 32,
        "candidate_payload_sha256": "b" * 64,
        "before": {
            "publication_id": before.publication_id,
            "publication_sha256": before.publication_sha256,
            "version_id": before.version_id,
            "version_sha256": before.version_sha256,
            "snapshot_sha256": before.content_sha256,
            "manifest_sha256": before.coverage_manifest_sha256,
            "binding_sha256": before.binding_sha256,
        },
        "after": {
            "snapshot_sha256": old_after.content_sha256,
            "store_snapshot_sha256": _sha(version_bytes),
            "manifest_sha256": old_after.coverage_manifest_sha256,
            "binding_sha256": old_after.binding_sha256,
        },
        "declaration_difference": diff,
    }
    policy = event["review_policy"]
    policy["snapshot_sha256"] = _sha(version_bytes)
    policy["difference"] = difference
    policy["difference_sha256"] = _sha(module._canonical(difference))
    _install_event(root, event)
    return store, before, event, version


def _install_event(root, event):
    event_bytes = module._canonical(event)
    (root / "publications" / "pub-v2.json").write_bytes(event_bytes)
    pointer = {
        "schema": "bt_api.calendar-store.current/1",
        "publication_id": "pub-v2",
        "publication_sha256": _sha(event_bytes),
    }
    (root / "CURRENT.json").write_bytes(module._canonical(pointer))
    receipt_path = root / "receipts" / "pub-v2.json"
    receipt = json.loads(receipt_path.read_bytes())
    receipt["pointer"] = pointer
    receipt_path.write_bytes(module._canonical(receipt))


def _rehash_diff(event):
    event["review_policy"]["difference_sha256"] = _sha(
        module._canonical(event["review_policy"]["difference"])
    )


def _rebind_version(root, event, version):
    content_sha = _sha(module._canonical(version["content"]))
    version["manifest"]["snapshot_sha256"] = content_sha
    version["manifest_sha256"] = _sha(module._canonical(version["manifest"]))
    version["binding"]["snapshot_sha256"] = content_sha
    version["binding"]["manifest_sha256"] = version["manifest_sha256"]
    version["binding_sha256"] = _sha(module._canonical(version["binding"]))
    version_bytes = module._canonical(version)
    (root / "versions" / "v2.json").write_bytes(version_bytes)
    event["content_sha256"] = content_sha
    event["version_sha256"] = _sha(version_bytes)
    policy = event["review_policy"]
    policy["snapshot_sha256"] = event["version_sha256"]
    diff = policy["difference"]
    diff["after"] = {
        "snapshot_sha256": content_sha,
        "store_snapshot_sha256": event["version_sha256"],
        "manifest_sha256": version["manifest_sha256"],
        "binding_sha256": version["binding_sha256"],
    }
    first = json.loads((root / "versions" / "v1.json").read_bytes())
    before = {**first["content"], "coverage": first["manifest"]["coverage"]}
    after = {**version["content"], "coverage": version["manifest"]["coverage"]}
    declared = diff["declaration_difference"]
    declared["before_sha256"] = _sha(module._canonical(before))
    declared["after_sha256"] = _sha(module._canonical(after))
    declared["changes"] = module._interruption_changes(before, after)
    declared["status"] = "CHANGED" if any(declared["changes"].values()) else "SAME"


def test_v7_read_consistent_receipts_and_real_ctp_binding(tmp_path):
    store, before, _, _ = _store_v7(tmp_path)
    result = store.read_current_with_consistent_receipts(
        budget=module.CalendarReadBudget(8 * 1024 * 1024, 64 * 1024 * 1024, 8)
    )
    current = result.snapshot
    assert current.snapshot_schema == "bt_api.calendar-store.snapshot/7"
    assert current.integrity_status == "STORE_INTEGRITY_ONLY"
    assert current.semantics_status == "SEMANTICS_NOT_VERIFIED"
    assert before.snapshot_schema == "bt_api.calendar-store.snapshot/2"
    bound = SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=store,
        snapshot_factory=CtpSnapshot,
        gate=FakeGate(),
        route="route-a",
        source_generation="gen-1",
        trading_day=FIRST,
        last_sequence=1,
    )
    view = bound.capture_read_view(mode="SIMULATION_ONLY")
    assert view.status().version_sha256 == current.version_sha256
    assert (
        view.query(
            venue="SHFE",
            kind="future",
            product="p",
            contract="mock_f1",
            trading_day=module.date(2026, 6, 2),
        ).result.status
        == "COMPLETE"
    )
    assert store.reconcile_publication("pub-v2").visibility == "COMMITTED"
    assert (
        store.history_at(module.datetime(2026, 6, 2, tzinfo=module.UTC)).status
        == "HISTORY_UNCERTAIN"
    )


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "extra",
        "wrong_before",
        "wrong_after",
        "omitted_change",
        "wrong_bounds",
        "mixed_source",
        "bad_time",
        "extra_exception_field",
        "missing_exception_field",
        "conflicting_scope",
        "snapshot_pair",
        "event_pair",
    ],
)
def test_v7_rejects_invalid_evidence(tmp_path, damage):
    store, _, event, version = _store_v7(tmp_path)
    difference = event["review_policy"]["difference"]
    if damage == "missing":
        difference.pop("before")
    elif damage == "extra":
        difference["extra"] = "x"
    elif damage == "wrong_before":
        difference["before"]["snapshot_sha256"] = "0" * 64
    elif damage == "wrong_after":
        difference["after"]["snapshot_sha256"] = "0" * 64
    elif damage == "omitted_change":
        difference["declaration_difference"]["changes"]["exceptions"].clear()
    elif damage == "wrong_bounds":
        difference["declaration_difference"]["changes"]["exceptions"][0]["after"]["end_utc"] = (
            "2026-06-02T02:45:00Z"
        )
    elif damage in {
        "mixed_source",
        "bad_time",
        "extra_exception_field",
        "missing_exception_field",
        "conflicting_scope",
        "snapshot_pair",
    }:
        if damage == "mixed_source":
            version["content"]["sources"].append({"id": "other", "description": "other"})
        elif damage == "bad_time":
            version["content"]["exceptions"][0]["start_utc"] = "2026-06-02T02:00:00+00:00"
        elif damage == "extra_exception_field":
            version["content"]["exceptions"][0]["extra"] = "x"
        elif damage == "missing_exception_field":
            version["content"]["exceptions"][0].pop("start_utc")
        elif damage == "conflicting_scope":
            row = dict(version["content"]["exceptions"][0])
            row["contract"] = "mock_f1"
            version["content"]["exceptions"].append(row)
        else:
            version["schema"] = "bt_api.calendar-store.snapshot/2"
        _rebind_version(tmp_path, event, version)
    elif damage == "event_pair":
        event["schema"] = "bt_api.calendar-store.publication/5"
    _rehash_diff(event)
    _install_event(tmp_path, event)
    with pytest.raises(module.CalendarStoreError):
        store.read_current()
    assert store.reconcile_publication("pub-v2").visibility == "COMMIT_UNKNOWN"


def test_v7_read_budget_and_missing_receipt_fail_closed(tmp_path):
    store, _, _, _ = _store_v7(tmp_path)
    with pytest.raises(module.CalendarStoreError):
        store.read_current_with_consistent_receipts(budget=module.CalendarReadBudget(1024, 2048, 1))
    (tmp_path / "receipts" / "pub-v2.json").unlink()
    with pytest.raises(module.CalendarStoreError):
        store.read_current_with_consistent_receipts()
    assert store.reconcile_publication("pub-v2").visibility == "COMMIT_UNKNOWN"


def test_store_integrity_does_not_prove_ctp_window_semantics(tmp_path):
    store, _, event, version = _store_v7(tmp_path)
    version["content"]["exceptions"][0]["end_utc"] = "2026-06-02T04:00:00Z"
    _rebind_version(tmp_path, event, version)
    _rehash_diff(event)
    _install_event(tmp_path, event)
    assert store.read_current().semantics_status == "SEMANTICS_NOT_VERIFIED"
    bound = SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=store,
        snapshot_factory=CtpSnapshot,
        gate=FakeGate(),
        route="route-a",
        source_generation="gen-1",
        trading_day=FIRST,
        last_sequence=1,
    )
    assert bound.status().load_error == "SNAPSHOT_VALIDATION_FAILED"


def test_v7_current_cannot_be_published_or_downgraded(tmp_path):
    store, _, _, _ = _store_v7(tmp_path)
    current = store.read_current()
    before = (tmp_path / "CURRENT.json").read_bytes()
    result = store.publish(
        current.content,
        publication_id="new-pub",
        version_id="new-version",
        coverage_manifest_bytes=current.coverage_manifest_bytes,
        coverage_manifest_sha256=current.coverage_manifest_sha256,
        binding_bytes=current.binding_bytes,
        binding_sha256=current.binding_sha256,
    )
    assert result.visibility == "NOT_COMMITTED"
    assert (tmp_path / "CURRENT.json").read_bytes() == before
    assert not (tmp_path / "versions" / "new-version.json").exists()


def test_old_archived_sdk_rejects_new_event_and_snapshot(tmp_path):
    _store_v7(tmp_path)
    repo = Path(__file__).resolve().parents[2]
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is needed to test the fixed archived SDK")
    archive = subprocess.run(
        [git, "archive", "d353c6e0", "bt_api_py"], cwd=repo, check=True, stdout=subprocess.PIPE
    ).stdout
    old_root = tmp_path / "old-sdk"
    old_root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(old_root, filter="data")
    code = """\nimport json, sys\nfrom pathlib import Path\nfrom bt_api_py.trading_calendar_store import TradingCalendarStore, _decode_version\nroot = Path(sys.argv[1])\nfor call in (lambda: TradingCalendarStore(root).read_current(), lambda: _decode_version(json.loads((root / "publications/pub-v2.json").read_bytes()), (root / "versions/v2.json").read_bytes())):\n    try: call()\n    except Exception: print("REJECTED")\n    else: print("ACCEPTED")\n"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(old_root)
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=old_root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == ["REJECTED", "REJECTED"]


def test_v7_bounded_history_can_find_version(tmp_path):
    from tests.unit.test_trading_calendar_store import _ManualClock

    start = module.datetime(2026, 1, 1, tzinfo=module.UTC)
    clock = _ManualClock(start)
    store, _, _, _ = _store_v7(tmp_path, clock)
    found = store.history_at(start + module.timedelta(seconds=6))
    assert found.status == "FOUND"
    assert found.snapshot.snapshot_schema == "bt_api.calendar-store.snapshot/7"
    assert found.snapshot.semantics_status == "SEMANTICS_NOT_VERIFIED"


def test_v7_manual_confirmation_and_auto_fields_are_checked(tmp_path):
    store, _, event, _ = _store_v7(tmp_path)
    policy = event["review_policy"]
    policy.update(
        {
            "mode": "manual",
            "mode_source": "explicit",
            "reviewer": "MOCK_reviewer",
            "confirmation_snapshot_sha256": policy["snapshot_sha256"],
            "confirmation_difference_sha256": policy["difference_sha256"],
            "confirmation_policy_revision": policy["policy_revision"],
            "confirmation_current": policy["baseline"],
            "confirmation_utc": "2026-06-02T00:00:00.000000Z",
        }
    )
    _install_event(tmp_path, event)
    assert store.read_current().snapshot_schema.endswith("/7")
    policy["confirmation_difference_sha256"] = "0" * 64
    _install_event(tmp_path, event)
    with pytest.raises(module.CalendarStoreError):
        store.read_current()
    policy["mode"] = "auto"
    policy["confirmation_difference_sha256"] = policy["difference_sha256"]
    _install_event(tmp_path, event)
    with pytest.raises(module.CalendarStoreError):
        store.read_current()


def test_multiple_v7_events_each_validate_full_prior_difference(tmp_path):
    from copy import deepcopy

    store, _, previous_event, version = _store_v7(tmp_path)
    before = store.read_current()
    version = deepcopy(version)
    version["content"]["exceptions"][0]["end_utc"] = "2026-06-02T02:30:00.000004Z"
    content_sha = _sha(module._canonical(version["content"]))
    version["manifest"]["snapshot_sha256"] = content_sha
    version["manifest_sha256"] = _sha(module._canonical(version["manifest"]))
    version["binding"]["snapshot_sha256"] = content_sha
    version["binding"]["manifest_sha256"] = version["manifest_sha256"]
    version["binding_sha256"] = _sha(module._canonical(version["binding"]))
    version_bytes = module._canonical(version)
    (tmp_path / "versions" / "v3.json").write_bytes(version_bytes)
    after_declaration = {
        "content": version["content"],
        "coverage": version["manifest"]["coverage"],
        "data_origin": "MOCK",
        "simulation_provenance": None,
    }
    declared = compare_simulated_interruption(
        _declaration(before),
        after_declaration,
        mode="SIMULATION_ONLY",
        snapshot_factory=CtpSnapshot,
    )
    assert declared["status"] == "CHANGED"
    event = deepcopy(previous_event)
    previous = {
        "publication_id": before.publication_id,
        "publication_sha256": before.publication_sha256,
    }
    event.update(
        publication_id="pub-v3",
        version_id="v3",
        sequence=3,
        previous=previous,
        content_sha256=content_sha,
        version_sha256=_sha(version_bytes),
    )
    policy = event["review_policy"]
    policy["baseline"] = {"mode": "MATCH", **previous}
    policy["snapshot_sha256"] = _sha(version_bytes)
    difference = policy["difference"]
    difference["before"] = {
        "publication_id": before.publication_id,
        "publication_sha256": before.publication_sha256,
        "version_id": before.version_id,
        "version_sha256": before.version_sha256,
        "snapshot_sha256": before.content_sha256,
        "manifest_sha256": before.coverage_manifest_sha256,
        "binding_sha256": before.binding_sha256,
    }
    difference["after"] = {
        "snapshot_sha256": content_sha,
        "store_snapshot_sha256": _sha(version_bytes),
        "manifest_sha256": version["manifest_sha256"],
        "binding_sha256": version["binding_sha256"],
    }
    difference["declaration_difference"] = declared
    _rehash_diff(event)
    encoded = module._canonical(event)
    (tmp_path / "publications" / "pub-v3.json").write_bytes(encoded)
    pointer = {
        "schema": "bt_api.calendar-store.current/1",
        "publication_id": "pub-v3",
        "publication_sha256": _sha(encoded),
    }
    (tmp_path / "CURRENT.json").write_bytes(module._canonical(pointer))
    receipt = json.loads((tmp_path / "receipts" / "pub-v2.json").read_bytes())
    receipt.update(publication_id="pub-v3", version_id="v3", pointer=pointer)
    (tmp_path / "receipts" / "pub-v3.json").write_bytes(module._canonical(receipt))
    assert store.read_current_with_consistent_receipts().snapshot.version_id == "v3"
    difference["declaration_difference"]["changes"]["exceptions"].clear()
    _rehash_diff(event)
    encoded = module._canonical(event)
    (tmp_path / "publications" / "pub-v3.json").write_bytes(encoded)
    pointer["publication_sha256"] = _sha(encoded)
    (tmp_path / "CURRENT.json").write_bytes(module._canonical(pointer))
    receipt["pointer"] = pointer
    (tmp_path / "receipts" / "pub-v3.json").write_bytes(module._canonical(receipt))
    with pytest.raises(module.CalendarStoreError):
        store.read_current()


def test_legacy_v6_event_remains_readable(tmp_path):
    from copy import deepcopy

    store = FixtureCalendarStore(tmp_path)
    first = fixture()
    first["exceptions"] = []
    publish(store, first, 1)
    before = store.read_current()
    second = deepcopy(first)
    second["trading_hours"][0]["day_windows"][0]["end"] = "10:30"
    publish(store, second, 2)
    after = store.read_current()
    event = json.loads((tmp_path / "publications" / "pub-v2.json").read_bytes())
    first_layers = {
        **before.content,
        "coverage": json.loads(before.coverage_manifest_bytes)["coverage"],
    }
    last_layers = {
        **after.content,
        "coverage": json.loads(after.coverage_manifest_bytes)["coverage"],
    }
    difference = {
        "schema": "bt_api.calendar-full-declaration-difference/1",
        "candidate_id": "a" * 32,
        "candidate_payload_sha256": "b" * 64,
        "before": {
            "publication_id": before.publication_id,
            "publication_sha256": before.publication_sha256,
            "version_id": before.version_id,
            "version_sha256": before.version_sha256,
            "snapshot_sha256": before.content_sha256,
            "manifest_sha256": before.coverage_manifest_sha256,
            "binding_sha256": before.binding_sha256,
        },
        "after": {
            "snapshot_sha256": after.content_sha256,
            "store_snapshot_sha256": after.version_sha256,
            "manifest_sha256": after.coverage_manifest_sha256,
            "binding_sha256": after.binding_sha256,
        },
        "changes": module._interruption_changes(first_layers, last_layers),
    }
    event["schema"] = "bt_api.calendar-store.publication/6"
    event["review_policy"]["difference"] = difference
    _rehash_diff(event)
    _install_event(tmp_path, event)
    loaded = store.read_current_with_consistent_receipts().snapshot
    assert (
        loaded.version_id == "v2" and loaded.snapshot_schema == "bt_api.calendar-store.snapshot/2"
    )
