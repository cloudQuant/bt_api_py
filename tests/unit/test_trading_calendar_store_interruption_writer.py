"""Internal /7 writer development tests, using only local MOCK declarations."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from bt_api_py import trading_calendar_store as m
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_interruption_view_simulation import fixture


def mock_content(interrupted=False):
    value = fixture()
    value["trading_days"] = [r for r in value["trading_days"] if r["day"] == "2026-06-02"]
    for row in value["trading_hours"]:
        row["valid_from"] = row["valid_to"] = "2026-06-02"
        row["night_windows"] = []
        row["night_mappings"] = [
            {
                "trading_day": "2026-06-02",
                "start_date": None,
                "source_ids": ["MOCK.simulation_only"],
            }
        ]
    value["exceptions"] = value["exceptions"][:1] if interrupted else []
    return value


def artifacts(content):
    digest = m._sha(m._canonical(content))
    manifest = m._canonical(
        {
            "schema": "bt_api.calendar-simulation-coverage/1",
            "snapshot_sha256": digest,
            "coverage": [
                {
                    "venue": "SHFE",
                    "coverage_start": "2026-06-02",
                    "coverage_end": "2026-06-02",
                    "semantics": "DECLARED_INCLUSIVE",
                }
            ],
        }
    )
    manifest_sha = m._sha(manifest)
    binding = m._canonical(
        {
            "schema": "bt_api.calendar-simulation-binding/1",
            "snapshot_sha256": digest,
            "manifest_sha256": manifest_sha,
        }
    )
    return {
        "coverage_manifest_bytes": manifest,
        "coverage_manifest_sha256": manifest_sha,
        "binding_bytes": binding,
        "binding_sha256": m._sha(binding),
    }


def start(root):
    store = FixtureCalendarStore(root)
    assert (
        store.publish(
            mock_content(), publication_id="p0", version_id="v0", **artifacts(mock_content())
        ).visibility
        == "COMMITTED"
    )
    return store


def request(store, content, publication_id="p1", version_id="v1"):
    before = store.read_current()
    a = artifacts(content)
    raw = m._decode(m._snapshot_bytes(m._content(content), *a.values()))
    raw["schema"] = m._SNAPSHOT_INTERRUPTION
    snapshot_hash = m._sha(m._canonical(raw))
    after = replace(
        before,
        content=content,
        content_sha256=m._sha(m._content(content)),
        coverage_manifest_bytes=a["coverage_manifest_bytes"],
        coverage_manifest_sha256=a["coverage_manifest_sha256"],
        binding_bytes=a["binding_bytes"],
        binding_sha256=a["binding_sha256"],
    )
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
            "snapshot_sha256": after.content_sha256,
            "store_snapshot_sha256": snapshot_hash,
            "manifest_sha256": after.coverage_manifest_sha256,
            "binding_sha256": after.binding_sha256,
        },
        "declaration_difference": m._interruption_difference(before, after),
    }
    policy = store.read_review_policy()
    precondition = m.CurrentPrecondition.matching(before.publication_id, before.publication_sha256)
    claim = m.PolicyPublication(
        policy.directory_id,
        policy.revision,
        policy.mode,
        snapshot_hash,
        difference,
        m._sha(m._canonical(difference)),
    )
    return {
        "mode": "SIMULATION_ONLY",
        "publication_id": publication_id,
        "version_id": version_id,
        **a,
        "expected_current": precondition,
        "policy_publication": claim,
        "locked_policy_guard": lambda policy: None,
    }


def test_v2_to_v7_then_modify_and_remove(tmp_path):
    store = start(tmp_path)
    for index, content in enumerate((mock_content(True), mock_content(True), mock_content()), 1):
        if index == 2:
            content["exceptions"][0]["end_utc"] = "2026-06-02T02:31:00.000003Z"
        args = request(store, content, f"p{index}", f"v{index}")
        result = store._publish_simulation_interruption(content, **args)
        assert result.visibility == "COMMITTED", result.detail
        assert (
            store.read_current_with_consistent_receipts().snapshot.snapshot_schema
            == m._SNAPSHOT_INTERRUPTION
        )
        event = json.loads((tmp_path / "publications" / f"p{index}.json").read_bytes())
        assert event["schema"] == m._EVENT_INTERRUPTION
        assert store._publish_simulation_interruption(content, **args).visibility == "COMMITTED"
    assert (
        store.publish(
            mock_content(), publication_id="p4", version_id="v4", **artifacts(mock_content())
        ).visibility
        == "NOT_COMMITTED"
    )


def test_actual_ctp_comparison_agrees_with_bound_declaration(tmp_path):
    from bt_api_ctp.calendar_snapshot import CalendarSnapshot

    from bt_api_py.trading_calendar_interruption_compare_simulation import (
        compare_simulated_interruption,
    )

    store = start(tmp_path)
    before, candidate = mock_content(), mock_content(True)
    coverage = json.loads(artifacts(before)["coverage_manifest_bytes"])["coverage"]
    declarations = [
        {
            "content": content,
            "coverage": coverage,
            "data_origin": "MOCK",
            "simulation_provenance": None,
        }
        for content in (before, candidate)
    ]
    expected = compare_simulated_interruption(
        *declarations, mode="SIMULATION_ONLY", snapshot_factory=CalendarSnapshot
    )
    assert expected["status"] == "CHANGED", expected
    args = request(store, candidate)
    assert args["policy_publication"].difference["declaration_difference"] == expected
    assert store._publish_simulation_interruption(candidate, **args).visibility == "COMMITTED"


@pytest.mark.parametrize("current_v7", [False, True])
@pytest.mark.parametrize("manual", [False, True])
def test_same_declaration_keeps_current_version_without_publication(tmp_path, current_v7, manual):
    store = start(tmp_path)
    content = mock_content()
    if current_v7:
        content = mock_content(True)
        args = request(store, content)
        assert store._publish_simulation_interruption(content, **args).visibility == "COMMITTED"
    if manual:
        change_mode(store, "manual")
    before = store.read_current()
    existing = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    args = request(store, content, "same-pub", "same-version")
    assert args["policy_publication"].difference["declaration_difference"]["status"] == "SAME"
    result = store._publish_simulation_interruption(content, **args)
    assert (result.visibility, result.version_id, result.fence) == (
        "UNCHANGED",
        before.version_id,
        "CLEAR",
    )
    assert store.read_current().snapshot_schema == before.snapshot_schema
    assert store.read_current().publication_id == before.publication_id
    assert {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == existing


@pytest.mark.parametrize("current_v7", [False, True])
def test_same_declaration_rejects_failed_guard_without_writes(tmp_path, current_v7):
    store = start(tmp_path)
    content = mock_content()
    if current_v7:
        content = mock_content(True)
        assert (
            store._publish_simulation_interruption(content, **request(store, content)).visibility
            == "COMMITTED"
        )
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    args = request(store, content, "same-pub", "same-version")

    def reject(policy):
        raise ValueError("guard rejected")

    args["locked_policy_guard"] = reject
    assert store._publish_simulation_interruption(content, **args).visibility == "NOT_COMMITTED"
    assert {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before


@pytest.mark.parametrize("mutation", ["missing", "extra", "utc", "baseline", "target", "guard"])
def test_bad_difference_or_guard_never_writes(tmp_path, mutation):
    store = start(tmp_path)
    content = mock_content(True)
    args = request(store, content)
    claim = args["policy_publication"]
    diff = deepcopy(claim.difference)
    if mutation == "missing":
        diff["declaration_difference"]["changes"]["exceptions"] = []
    elif mutation == "extra":
        diff["declaration_difference"]["extra"] = "unexpected"
    elif mutation == "utc":
        diff["declaration_difference"]["changes"]["exceptions"][0]["after"]["end_utc"] = (
            "2026-06-02T02:40:00Z"
        )
    elif mutation == "baseline":
        diff["before"]["version_sha256"] = "f" * 64
    elif mutation == "target":
        diff["after"]["binding_sha256"] = "f" * 64
    else:

        def reject(policy):
            raise RuntimeError("reject")

        args["locked_policy_guard"] = reject
    args["policy_publication"] = replace(
        claim, difference=diff, difference_sha256=m._sha(m._canonical(diff))
    )
    result = store._publish_simulation_interruption(content, **args)
    assert result.visibility == "NOT_COMMITTED"
    assert store.read_current().publication_id == "p0"
    assert not (tmp_path / "versions" / "v1.json").exists()


def change_mode(store, mode):
    policy = store.read_review_policy()
    result = store.change_review_policy(
        expected_revision=policy.revision,
        mode=mode,
        operation_id="mode1",
        actor="operator1",
        at_utc="2026-01-01T00:00:00.000000Z",
    )
    assert result.status == "COMMITTED"


def test_manual_confirmation_is_bound_to_current_policy_and_complete_diff(tmp_path):
    store = start(tmp_path)
    change_mode(store, "manual")
    content = mock_content(True)
    args = request(store, content)
    claim = args["policy_publication"]
    assert store._publish_simulation_interruption(content, **args).visibility == "NOT_COMMITTED"
    confirmed = replace(
        claim,
        reviewer="reviewer1",
        confirmation_snapshot_sha256=claim.snapshot_sha256,
        confirmation_difference_sha256=claim.difference_sha256,
        confirmation_policy_revision=claim.policy_revision,
        confirmation_current=args["expected_current"],
        confirmation_utc="2026-01-01T00:00:00.000000Z",
    )
    for invalid in (
        replace(confirmed, confirmation_snapshot_sha256="f" * 64),
        replace(confirmed, confirmation_difference_sha256="f" * 64),
        replace(confirmed, confirmation_policy_revision="other"),
        replace(confirmed, confirmation_current=m.CurrentPrecondition.absent()),
        replace(confirmed, confirmation_utc=None),
    ):
        args["policy_publication"] = invalid
        assert store._publish_simulation_interruption(content, **args).visibility == "NOT_COMMITTED"
    args["policy_publication"] = confirmed
    assert store._publish_simulation_interruption(content, **args).visibility == "COMMITTED"


def test_auto_rejects_claimed_human_review_and_stale_policy(tmp_path):
    store = start(tmp_path)
    content = mock_content(True)
    args = request(store, content)
    claim = args["policy_publication"]
    args["policy_publication"] = replace(claim, reviewer="reviewer1")
    assert store._publish_simulation_interruption(content, **args).visibility == "NOT_COMMITTED"
    args["policy_publication"] = claim
    change_mode(store, "manual")
    assert store._publish_simulation_interruption(content, **args).visibility == "NOT_COMMITTED"
    assert not (tmp_path / "versions" / "v1.json").exists()


def test_wrong_mode_or_missing_guard_rejected(tmp_path):
    store = start(tmp_path)
    content = mock_content(True)
    args = request(store, content)
    for change in ({"mode": "LIVE"}, {"locked_policy_guard": None}):
        assert (
            store._publish_simulation_interruption(content, **{**args, **change}).visibility
            == "NOT_COMMITTED"
        )
    assert not (tmp_path / "versions" / "v1.json").exists()


def test_mixed_source_and_scope_change_fail_before_write(tmp_path):
    store = start(tmp_path)
    content = mock_content(True)
    args = request(store, content)
    mixed, altered = deepcopy(content), deepcopy(content)
    mixed["sources"].append({"id": "other", "description": "other"})
    altered["trading_hours"][0]["contracts"].append("mock_new")
    for candidate in (mixed, altered):
        a = artifacts(candidate)
        raw = m._decode(m._snapshot_bytes(m._content(candidate), *a.values()))
        raw["schema"] = m._SNAPSHOT_INTERRUPTION
        snapshot_hash = m._sha(m._canonical(raw))
        diff = deepcopy(args["policy_publication"].difference)
        diff["after"] = {
            "snapshot_sha256": m._sha(m._content(candidate)),
            "store_snapshot_sha256": snapshot_hash,
            "manifest_sha256": a["coverage_manifest_sha256"],
            "binding_sha256": a["binding_sha256"],
        }
        claim = replace(
            args["policy_publication"],
            snapshot_sha256=snapshot_hash,
            difference=diff,
            difference_sha256=m._sha(m._canonical(diff)),
        )
        result = store._publish_simulation_interruption(
            candidate, **{**args, **a, "policy_publication": claim}
        )
        assert result.visibility == "NOT_COMMITTED"
    assert not (tmp_path / "versions" / "v1.json").exists()


def test_same_publication_id_with_different_content_is_conflict(tmp_path):
    store = start(tmp_path)
    content = mock_content(True)
    args = request(store, content)
    assert store._publish_simulation_interruption(content, **args).visibility == "COMMITTED"
    altered = deepcopy(content)
    altered["exceptions"][0]["end_utc"] = "2026-06-02T02:31:00.000003Z"
    # The operation identity is already consumed; a modified claim cannot replay it.
    assert store._publish_simulation_interruption(altered, **args).visibility in (
        "ID_CONFLICT",
        "COMMIT_UNKNOWN",
    )


def test_stale_current_or_missing_receipt_cannot_publish(tmp_path):
    store = start(tmp_path)
    content = mock_content(True)
    args = request(store, content)
    args["expected_current"] = m.CurrentPrecondition.matching("other", "f" * 64)
    assert store._publish_simulation_interruption(content, **args).visibility == "NOT_COMMITTED"
    args = request(store, content)
    (tmp_path / "receipts" / "p0.json").unlink()
    assert store._publish_simulation_interruption(content, **args).visibility == "NOT_COMMITTED"
    assert not (tmp_path / "versions" / "v1.json").exists()


def test_missing_current_cannot_be_first_interruption_publication(tmp_path):
    store = start(tmp_path)
    content = mock_content(True)
    args = request(store, content)
    (tmp_path / "CURRENT.json").unlink()
    result = store._publish_simulation_interruption(content, **args)
    assert result.visibility == "NOT_COMMITTED"
    assert not (tmp_path / "versions" / "v1.json").exists()


def test_lock_busy_cannot_publish(tmp_path):
    import fcntl

    store = start(tmp_path)
    content = mock_content(True)
    args = request(store, content)
    with (tmp_path / ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = store._publish_simulation_interruption(content, **args)
    assert result.visibility == "COMMIT_UNKNOWN"
    assert not (tmp_path / "versions" / "v1.json").exists()


@pytest.mark.parametrize("failure", ["before_replace", "after_replace", "receipt"])
def test_interruption_failure_original_id_can_be_reconciled(tmp_path, monkeypatch, failure):
    store = start(tmp_path)
    content = mock_content(True)
    args = request(store, content)
    real_replace, real_write = m.os.replace, store._write_new

    def maybe_replace(src, dst):
        if failure == "before_replace":
            raise OSError("injected")
        real_replace(src, dst)
        if failure == "after_replace":
            raise OSError("injected")

    def maybe_write(path, data):
        if failure == "receipt" and path == tmp_path / "receipts" / "p1.json":
            raise OSError("injected")
        return real_write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(m.os, "replace", maybe_replace)
        patch.setattr(store, "_write_new", maybe_write)
        result = store._publish_simulation_interruption(content, **args)
    assert result.visibility in ("COMMIT_UNKNOWN", "COMMITTED")
    outcome = store.reconcile_publication("p1")
    if failure == "before_replace":
        assert outcome.visibility == "COMMIT_UNKNOWN"
        assert store.read_current().publication_id == "p0"
    else:
        assert outcome.visibility == "COMMITTED"
        assert store.read_current_with_consistent_receipts().snapshot.publication_id == "p1"
