"""Offline configured simulation updates from a maintained supplement."""

import json

import pytest

from bt_api_py.trading_calendar_mock import SOURCE
from tests.unit.test_trading_calendar_mock_cli_integration import SMALL_MOCK, command
from tests.unit.test_trading_calendar_mock_persistent import operation, run_args, settings
from tests.unit.test_trading_calendar_observation_run import env_without_network


@pytest.mark.parametrize("kind", ["SOURCE", "DAY", "TEMPLATE", "NIGHT_MAPPING"])
def test_conflict_types_remain_value_errors(kind):
    from bt_api_py.trading_calendar_review_supplement import SupplementConflictError
    from tests.unit.test_trading_calendar_review_supplement import inputs, merge

    observed, extra = inputs()
    if kind == "SOURCE":
        extra["sources"][0]["description"] = "different"
    elif kind == "DAY":
        extra["trading_days"][0]["status"] = "open"
    elif kind == "TEMPLATE":
        extra["trading_hours"][0]["day_windows"][0]["end"] = "11:00"
    else:
        observed["trading_days"][0]["status"] = "open"
        extra["trading_days"][0]["status"] = "open"
        observed["trading_hours"][0]["night_mappings"] = [
            {"trading_day": "2026-01-01", "start_date": None, "source_ids": ["synthetic"]}
        ]
        row = extra["trading_hours"][0]
        row["night_windows"] = [
            {
                "id": "night",
                "phase": "continuous",
                "start": "21:00",
                "end": "23:00",
                "end_day_offset": 0,
            }
        ]
        row["night_mappings"] = [
            {"trading_day": "2026-01-01", "start_date": "2025-12-31", "source_ids": ["manual"]}
        ]
    with pytest.raises(ValueError) as error:
        merge(observed, extra)
    assert isinstance(error.value, SupplementConflictError) and error.value.code == kind


def test_derived_conflict_classification_is_explicit_and_old_reason_remains(tmp_path):
    from bt_api_py import trading_calendar_review_bundle as review
    from tests.unit.test_trading_calendar_review_bundle import sample

    fs = "TRUSTED_LOCAL_POSIX"
    parent_id, child_id = "1" * 32, "2" * 32
    parent = review.build_once(
        root=str(tmp_path),
        candidate_id=parent_id,
        input_data=sample(),
        filesystem=fs,
        emit=lambda _: None,
    )
    assert parent["status"] == "COMMITTED"
    shown = review.show(root=str(tmp_path), candidate_id=parent_id, filesystem=fs)
    kwargs = {
        "root": str(tmp_path),
        "candidate_id": child_id,
        "parent_id": parent_id,
        "parent_sha256": shown["payload_sha256"],
        "filesystem": fs,
        "emit": lambda _: None,
        "source_texts": {},
        "decisions": [],
        "supplement": {
            "sources": [{"id": "synthetic", "description": "fictional"}],
            "trading_days": [
                {
                    "venue": "SHFE",
                    "day": "2026-01-01",
                    "status": "open",
                    "source_ids": ["synthetic"],
                }
            ],
            "trading_hours": [],
            "exceptions": [],
        },
    }
    legacy = review.build_derived_once(**kwargs)
    assert legacy["status"] == "NOT_COMMITTED" and legacy["reason"] == "INPUT_UNAVAILABLE"
    classified = review.build_derived_once(**kwargs, classify_conflicts=True)
    assert classified["reason"] == "SUPPLEMENT_CONFLICT" and classified["conflict_kind"] == "DAY"


OPEN_MOCK = (
    SMALL_MOCK
    + """
original_build = mock.build
def open_mock(year):
    data, supplement = original_build(year)
    for shard in data['shards']:
        day = shard['layers']['trading_days'][0]
        day['status'] = 'open'
        for row in shard['layers']['trading_hours']:
            row['night_mappings'] = [{'trading_day': day['day'], 'start_date': None, 'source_ids': [mock.SOURCE['id']]}]
    supplement['trading_days'][0]['status'] = 'open'
    return data, supplement
mock.build = open_mock
"""
)


def supplemental(*, conflict=False):
    return {
        "sources": [SOURCE],
        "trading_days": (
            [
                {
                    "venue": "SHFE",
                    "day": "2026-01-01",
                    "status": "closed",
                    "source_ids": [SOURCE["id"]],
                }
            ]
            if conflict
            else []
        ),
        "trading_hours": [],
        "exceptions": (
            []
            if conflict
            else [
                {
                    "venue": "SHFE",
                    "kind": "future",
                    "product": "pfuture",
                    "contract": "shfefuture2701",
                    "trading_day": "2026-01-01",
                    "action": "close_day",
                    "window_id": None,
                    "source_ids": [SOURCE["id"]],
                }
            ]
        ),
    }


def baseline_supplement():
    return {
        "sources": [SOURCE],
        "trading_days": [
            {"venue": "SHFE", "day": "2026-01-01", "status": "open", "source_ids": [SOURCE["id"]]}
        ],
        "trading_hours": [],
        "exceptions": [],
    }


def test_auto_supplement_add_same_remove_and_invalid(tmp_path):
    from bt_api_py.trading_calendar_store import TradingCalendarStore

    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    run_id = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    path = root / "supplement.yaml"
    path.write_text(json.dumps(supplemental()))
    rc, changed, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc == 0, (changed, err)
    assert changed[-1]["supplement_status"] == "CAPTURED_UNVERIFIED"
    store = TradingCalendarStore(root)
    snapshot = store.read_current()
    assert snapshot.content["exceptions"] == supplemental()["exceptions"]
    difference = store.read_publication_policy(snapshot.publication_id)["difference"]
    assert any(row["operation"] == "added" for row in difference["changes"]["exceptions"])
    pointer = (root / "CURRENT.json").read_bytes()
    counts = tuple(len(list((root / name).iterdir())) for name in ("versions", "publications"))
    rc, same, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc == 10 and same[-1]["status"] == "SAME", (same, err)
    assert (root / "CURRENT.json").read_bytes() == pointer
    assert (
        tuple(len(list((root / name).iterdir())) for name in ("versions", "publications")) == counts
    )
    previous = (root / "CURRENT.json").read_bytes()
    path.write_text(json.dumps(supplemental(conflict=True)))
    rc, conflict, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc == 24 and conflict[-1]["reason"] == "SUPPLEMENT_CONFLICT", (conflict, err)
    assert conflict[-1]["conflict_kind"] == "DAY"
    assert (root / "CURRENT.json").read_bytes() == previous
    foreign = supplemental()
    foreign["sources"][0] = {"id": "real_source", "description": "external"}
    foreign["exceptions"][0]["source_ids"] = ["real_source"]
    path.write_text(json.dumps(foreign))
    rc, rejected, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc != 0 and (root / "CURRENT.json").read_bytes() == previous, (rejected, err)
    path.write_text("not: [valid")
    rc, invalid, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc != 0 and (root / "CURRENT.json").read_bytes() == previous, (invalid, err)
    assert invalid[-1]["supplement_status"] == "INVALID_CONTENT", (invalid, err)
    path.unlink()
    rc, removed, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc == 24 and removed[-1]["reason"] == "SUPPLEMENT_MISSING", (removed, err)
    assert (root / "CURRENT.json").read_bytes() == previous
    path.write_text(json.dumps(baseline_supplement()))
    rc, removed, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc == 0 and removed[-1]["supplement_status"] == "CAPTURED_UNVERIFIED", (removed, err)
    snapshot = store.read_current()
    assert snapshot.content["exceptions"] == []
    difference = store.read_publication_policy(snapshot.publication_id)["difference"]
    assert any(row["operation"] == "removed" for row in difference["changes"]["exceptions"])


def confirm_args(root, pending):
    return operation(
        root,
        pending["run_id"],
        "mock-confirm",
        "--review-id",
        pending["review_id"],
        "--review-sha256",
        pending["review_payload_sha256"],
        "--reviewer",
        "MOCK_reviewer",
        "--confirmed-at",
        "2026-11-13T00:00:00.000000Z",
    )


def test_manual_review_binds_capture_and_rechecks_input(tmp_path):
    root, config = settings(tmp_path, "manual")
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 10, (lines, err)
    initial = lines[-1]
    rc, confirmed, err = command(env, *confirm_args(root, initial))
    assert rc == 0, (confirmed, err)
    original_run = initial["run_id"]
    path = root / "supplement.yaml"
    path.write_text(json.dumps(supplemental()))
    rc, changed, err = command(env, *operation(root, original_run, "mock-update"))
    assert rc == 10 and changed[-1]["status"] == "PENDING_REVIEW", (changed, err)
    pending = changed[-1]
    assert pending["run_id"] != original_run
    assert pending["proposal"]["difference"]["changes"]["exceptions"]
    assert str(path) not in json.dumps(changed)
    rc, shown, err = command(
        env, *operation(root, pending["run_id"], "mock-show", "--review-id", pending["review_id"])
    )
    assert rc == 0 and str(path) not in json.dumps(shown), (shown, err)
    previous = (root / "CURRENT.json").read_bytes()
    invalid_confirmation = list(confirm_args(root, pending))
    invalid_confirmation[invalid_confirmation.index("--reviewer") + 1] = "invalid"
    rc, invalid, err = command(env, *invalid_confirmation)
    assert rc == 24 and invalid[-1]["reason"] == "INVALID_CONFIRMATION", (invalid, err)
    assert not (
        root / "review-bundles" / f"mock-confirm-intent-{pending['review_id']}.json"
    ).exists()
    path.write_text("invalid: [yaml")
    rc, unknown, err = command(env, *confirm_args(root, pending))
    assert rc == 25 and unknown[-1]["reason"] == "SUPPLEMENT_UNAVAILABLE", (unknown, err)
    assert not (root / "review-bundles" / f"mock-review-stale-{pending['review_id']}.json").exists()
    path.write_text(json.dumps(supplemental()))
    rc, retry, err = command(env, *confirm_args(root, pending))
    assert rc == 25 and retry[-1]["reason"] == "CONFIRMATION_RECONCILE_FIRST", (retry, err)
    rc, fresh, err = command(env, *operation(root, original_run, "mock-update"))
    assert rc == 10 and fresh[-1]["status"] == "PENDING_REVIEW", (fresh, err)
    pending = fresh[-1]
    path.write_text(json.dumps(supplemental(conflict=True)))
    rc, rejected, err = command(env, *confirm_args(root, pending))
    assert rc == 24 and rejected[-1]["reason"] == "REVIEW_STALE", (rejected, err)
    rc, shown, err = command(
        env, *operation(root, pending["run_id"], "mock-show", "--review-id", pending["review_id"])
    )
    assert rc == 0 and shown[-1]["review_stale"]["status"] == "VISIBLE_UNAUTHENTICATED", (
        shown,
        err,
    )
    assert (root / "CURRENT.json").read_bytes() == previous
    path.write_text(json.dumps(supplemental()))
    rc, confirmed, err = command(env, *confirm_args(root, pending))
    assert rc == 24 and confirmed[-1]["reason"] == "REVIEW_STALE", (confirmed, err)
    rc, changed, err = command(env, *operation(root, original_run, "mock-update"))
    assert rc == 10 and changed[-1]["status"] == "PENDING_REVIEW", (changed, err)
    rc, confirmed, err = command(env, *confirm_args(root, changed[-1]))
    assert rc == 0 and confirmed[-1]["status"] == "COMMITTED", (confirmed, err)
    path.write_text(json.dumps(baseline_supplement()))
    rc, removal, err = command(env, *operation(root, original_run, "mock-update"))
    assert rc == 10 and removal[-1]["status"] == "PENDING_REVIEW", (removal, err)
    assert any(
        row["operation"] == "removed"
        for row in removal[-1]["proposal"]["difference"]["changes"]["exceptions"]
    )
    path.write_text(json.dumps(supplemental()))
    rc, refused, err = command(env, *confirm_args(root, removal[-1]))
    assert rc == 24 and refused[-1]["reason"] == "REVIEW_STALE", (refused, err)
    path.write_text(json.dumps(baseline_supplement()))
    rc, confirmed, err = command(env, *confirm_args(root, removal[-1]))
    assert rc == 24 and confirmed[-1]["reason"] == "REVIEW_STALE", (confirmed, err)
    rc, removal, err = command(env, *operation(root, original_run, "mock-update"))
    assert rc == 10 and removal[-1]["status"] == "PENDING_REVIEW", (removal, err)
    rc, confirmed, err = command(env, *confirm_args(root, removal[-1]))
    assert rc == 0 and confirmed[-1]["status"] == "COMMITTED", (confirmed, err)


def test_uncertain_audit_returns_original_ids_for_read_only_inspection(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    original = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    (root / "supplement.yaml").write_text(json.dumps(supplemental()))
    injection = (
        OPEN_MOCK
        + """
from bt_api_py import trading_calendar_observation_run as run_module
original_write = run_module._write_once
def uncertain(directory, chain, name, data):
    state, reason = original_write(directory, chain, name, data)
    if name.startswith('mock-') and state == 'COMMITTED':
        return 'COMMIT_UNKNOWN', 'INJECTED_UNKNOWN'
    return state, reason
run_module._write_once = uncertain
"""
    )
    injection_dir = tmp_path / "second-process"
    injection_dir.mkdir()
    injected_env = env_without_network(injection_dir, injection)
    rc, result, err = command(injected_env, *operation(root, original, "mock-update"))
    assert rc == 25 and result[-1]["reason"] == "AUDIT_RECONCILE_FIRST", (result, err)
    allocated, outcome = result[0], result[-1]
    assert allocated["new_run_id"] == outcome["run_id"]
    assert allocated["capture_id"] == outcome["capture_id"]
    rc, shown, err = command(
        env,
        "show",
        "--calendar-root",
        str(root),
        "--run-id",
        outcome["run_id"],
        "--trusted-local-posix",
    )
    assert shown[-1]["mock_audit"]["state"] == "VISIBLE_UNAUTHENTICATED", (shown, err)
    assert shown[-1]["mock_audit"]["capture_id"] == outcome["capture_id"]
    assert not (root / "supplement-captures" / f"capture-{outcome['capture_id']}.json").exists()


@pytest.mark.parametrize("prior_same", [False, True])
def test_auto_rechecks_after_capture_before_publish(tmp_path, prior_same):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    original = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    path = root / "supplement.yaml"
    path.write_text(json.dumps(supplemental()))
    if prior_same:
        rc, installed, err = command(env, *operation(root, original, "mock-update"))
        assert rc == 0 and installed[-1]["status"] == "COMMITTED", (installed, err)
    pointer = (root / "CURRENT.json").read_bytes()
    claims = set((root / "observation-runs").glob("mock-publication-*.json"))
    folder = tmp_path / "changed-input"
    folder.mkdir()
    injection = (
        OPEN_MOCK
        + f"""
from bt_api_py import trading_calendar_mock_cli as cli
original_act = cli._act
def change_after_capture(operator, ids, proposal, **kwargs):
    from pathlib import Path
    (Path(operator.root) / 'supplement.yaml').write_text({json.dumps(json.dumps(supplemental(conflict=True)))})
    return original_act(operator, ids, proposal, **kwargs)
cli._act = change_after_capture
"""
    )
    rc, result, err = command(
        env_without_network(folder, injection), *operation(root, original, "mock-update")
    )
    assert rc == 24 and result[-1]["reason"] == "INPUT_CHANGED", (result, err)
    assert (root / "CURRENT.json").read_bytes() == pointer
    assert set((root / "observation-runs").glob("mock-publication-*.json")) == claims


def test_missing_at_check_never_publishes_even_if_file_appears(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    original = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    pointer = (root / "CURRENT.json").read_bytes()
    claims = set((root / "observation-runs").glob("mock-publication-*.json"))
    folder = tmp_path / "appeared-input"
    folder.mkdir()
    target = str(root / "supplement.yaml")
    injection = (
        OPEN_MOCK
        + f"""
import os
original_lstat = os.lstat
def appear_on_check(path, *args, **kwargs):
    if os.fspath(path) == {target!r}:
        with open(path, 'w') as output: output.write({json.dumps(json.dumps(supplemental()))})
        raise FileNotFoundError('injected')
    return original_lstat(path, *args, **kwargs)
os.lstat = appear_on_check
"""
    )
    rc, result, err = command(
        env_without_network(folder, injection), *operation(root, original, "mock-update")
    )
    assert rc == 24 and result[-1]["reason"] == "SUPPLEMENT_MISSING", (result, err)
    assert (root / "supplement.yaml").exists()
    assert (root / "CURRENT.json").read_bytes() == pointer
    assert set((root / "observation-runs").glob("mock-publication-*.json")) == claims


def test_manual_rechecks_again_after_prepare(tmp_path):
    root, config = settings(tmp_path, "manual")
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 10, (lines, err)
    initial = lines[-1]
    rc, result, err = command(env, *confirm_args(root, initial))
    assert rc == 0, (result, err)
    (root / "supplement.yaml").write_text(json.dumps(supplemental()))
    rc, result, err = command(env, *operation(root, initial["run_id"], "mock-update"))
    assert rc == 10 and result[-1]["status"] == "PENDING_REVIEW", (result, err)
    pending = result[-1]
    pointer = (root / "CURRENT.json").read_bytes()
    folder = tmp_path / "changed-after-prepare"
    folder.mkdir()
    injection = (
        OPEN_MOCK
        + f"""
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
original_prepare = MockCalendarPublication.prepare
def changed_after_prepare(self, **kwargs):
    result = original_prepare(self, **kwargs)
    from pathlib import Path
    (Path(self.root) / 'supplement.yaml').write_text({json.dumps(json.dumps(supplemental(conflict=True)))})
    return result
MockCalendarPublication.prepare = changed_after_prepare
"""
    )
    rc, result, err = command(env_without_network(folder, injection), *confirm_args(root, pending))
    assert rc == 24 and result[-1]["reason"] == "REVIEW_STALE", (result, err)
    assert (root / "CURRENT.json").read_bytes() == pointer
    assert not (
        root / "observation-runs" / f"mock-publication-{pending['publication_id']}.json"
    ).exists()


def test_explicit_supplement_overrides_invalid_default_without_path_output(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    run_id = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    (root / "supplement.yaml").write_text("invalid: [yaml")
    outside = root.parent / "maintainer-input.yaml"
    outside.write_text(json.dumps(supplemental()))
    rc, result, err = command(
        env, *operation(root, run_id, "mock-update", "--supplement", str(outside))
    )
    assert rc == 0 and result[-1]["status"] == "COMMITTED", (result, err)
    assert str(outside) not in json.dumps(result)
    pointer = (root / "CURRENT.json").read_bytes()
    outside.unlink()
    rc, result, err = command(
        env, *operation(root, run_id, "mock-update", "--supplement", str(outside))
    )
    assert rc == 24 and result[-1]["reason"] == "SUPPLEMENT_MISSING", (result, err)
    assert (
        str(outside) not in json.dumps(result) and (root / "CURRENT.json").read_bytes() == pointer
    )
    outside.symlink_to(root / "supplement.yaml")
    rc, result, err = command(
        env, *operation(root, run_id, "mock-update", "--supplement", str(outside))
    )
    assert rc == 25 and result[-1]["supplement_status"] == "SYMLINK", (result, err)
    assert result[-1]["reason"] != "SUPPLEMENT_CONFLICT"
    assert str(outside) not in json.dumps(result)
    assert (root / "CURRENT.json").read_bytes() == pointer


def test_manual_current_change_is_stale_even_if_pointer_restored(tmp_path):
    root, config = settings(tmp_path, "manual")
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 10, (lines, err)
    initial = lines[-1]
    rc, result, err = command(env, *confirm_args(root, initial))
    assert rc == 0, (result, err)
    previous = (root / "CURRENT.json").read_bytes()
    (root / "supplement.yaml").write_text(json.dumps(supplemental()))
    rc, first, err = command(env, *operation(root, initial["run_id"], "mock-update"))
    assert rc == 10 and first[-1]["status"] == "PENDING_REVIEW", (first, err)
    rc, second, err = command(env, *operation(root, initial["run_id"], "mock-update"))
    assert rc == 10 and second[-1]["status"] == "PENDING_REVIEW", (second, err)
    rc, confirmed, err = command(env, *confirm_args(root, second[-1]))
    assert rc == 0, (confirmed, err)
    latest = (root / "CURRENT.json").read_bytes()
    rc, stale, err = command(env, *confirm_args(root, first[-1]))
    assert rc == 24 and stale[-1]["stale_reason"] == "BASELINE_CHANGED", (stale, err)
    try:
        (root / "CURRENT.json").write_bytes(previous)
        rc, stale, err = command(env, *confirm_args(root, first[-1]))
        assert rc == 24 and stale[-1]["reason"] == "REVIEW_STALE", (stale, err)
    finally:
        (root / "CURRENT.json").write_bytes(latest)
