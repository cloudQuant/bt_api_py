"""Offline policy transitions on a real isolated local calendar store."""

import json
import subprocess
import sys
import time

from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation, run_args, settings
from tests.unit.test_trading_calendar_mock_supplement_update import OPEN_MOCK, supplemental
from tests.unit.test_trading_calendar_observation_run import env_without_network


def set_mode(config, root, mode):
    calendar = {"root": str(root)}
    if mode is not None:
        calendar["review_mode"] = mode
    config.write_text(json.dumps({"calendars": calendar}))


def policy(root, run_id, name, *extra):
    return operation(root, run_id, "mock-policy-" + name, *extra)


def begin(env, root, run_id, mode, operation_id, mode_source=None):
    return command(
        env,
        *policy(
            root,
            run_id,
            "begin",
            "--mode",
            mode,
            "--actor",
            "MOCK_reviewer",
            "--at-utc",
            "2026-11-13T00:00:00.000000Z",
            "--operation-id",
            operation_id,
            *(("--mode-source", mode_source) if mode_source is not None else ()),
        ),
    )


def test_policy_both_directions_block_old_candidates_and_preserve_current(tmp_path):
    root, config = settings(tmp_path)
    set_mode(config, root, None)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    run_id = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    store = TradingCalendarStore(root)
    assert store.read_review_policy().mode_source == "default"
    (root / "supplement.yaml").write_text(json.dumps(supplemental()))
    before = (root / "CURRENT.json").read_bytes()
    publications = set((root / "publications").iterdir())

    set_mode(config, root, "manual")
    rc, mismatch, err = begin(env, root, run_id, "manual", "MOCK_config_mismatch")
    assert rc == 25 and mismatch[-1]["status"] == "UNKNOWN", (mismatch, err)
    assert not (root / "POLICY_CHANGE_PENDING.json").exists()
    assert (root / "CURRENT.json").read_bytes() == before
    set_mode(config, root, None)
    rc, started, err = begin(env, root, run_id, "manual", "MOCK_auto_to_manual")
    assert rc == 10 and started[-1]["status"] == "PENDING_CONFIG", (started, err)
    assert (root / "CURRENT.json").read_bytes() == before
    rc, blocked, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc != 0 and (root / "CURRENT.json").read_bytes() == before, (blocked, err)
    rc, pending, err = command(
        env, *policy(root, run_id, "show", "--operation-id", "MOCK_auto_to_manual")
    )
    assert rc == 10 and pending[-1]["status"] == "PENDING_CONFIG", (pending, err)
    saved_config = config.read_bytes()
    config.unlink()
    rc, missing, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_auto_to_manual")
    )
    assert rc == 25 and (root / "CURRENT.json").read_bytes() == before, (missing, err)
    assert missing[-1]["config_reason"] == "CONFIG_READ_FAILED"
    rc, missing_shown, err = command(
        env, *policy(root, run_id, "show", "--operation-id", "MOCK_auto_to_manual")
    )
    assert rc == 10 and missing_shown[-1]["config_reason"] == "CONFIG_READ_FAILED", (
        missing_shown,
        err,
    )
    config.write_text("secret_untrusted: [bad")
    rc, malformed, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_auto_to_manual")
    )
    assert rc == 25 and malformed[-1]["config_reason"] == "CONFIG_YAML_INVALID", (malformed, err)
    assert "secret_untrusted" not in json.dumps(malformed)
    config.write_bytes(saved_config)
    rc, wrong, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_auto_to_manual")
    )
    assert rc == 25 and (root / "CURRENT.json").read_bytes() == before, (wrong, err)
    set_mode(config, root, "manual")
    rc, finished, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_auto_to_manual")
    )
    assert rc == 0 and finished[-1]["policy"]["mode"] == "manual", (finished, err)
    assert (root / "CURRENT.json").read_bytes() == before
    assert set((root / "publications").iterdir()) == publications
    rc, replay, err = begin(env, root, run_id, "auto", "MOCK_auto_to_manual")
    assert rc == 24 and replay[-1]["status"] == "NOT_COMMITTED", (replay, err)
    assert not (root / "POLICY_CHANGE_PENDING.json").exists()
    from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication

    operator = MockCalendarPublication(root=str(root), run_id=run_id)
    policy_now = operator._store.read_review_policy()
    bypass = operator.change_policy(
        expected_revision=policy_now.revision,
        mode="auto",
        operation_id="MOCK_bypass",
        actor="MOCK_reviewer",
        at_utc="2026-11-13T00:00:00.000000Z",
        emit=lambda _: None,
    )
    assert bypass["status"] == "NOT_COMMITTED" and bypass["reason"] == "POLICY_CHANGE_NOT_SUPPORTED"
    assert operator._store.read_review_policy() == policy_now
    rc, review, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc == 10 and review[-1]["status"] == "PENDING_REVIEW", (review, err)
    old_review = review[-1]

    rc, started, err = begin(env, root, run_id, "auto", "MOCK_manual_to_auto")
    assert rc == 10, (started, err)
    set_mode(config, root, "auto")
    rc, finished, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_manual_to_auto")
    )
    assert rc == 0 and finished[-1]["policy"]["mode"] == "auto", (finished, err)
    assert (root / "CURRENT.json").read_bytes() == before
    assert set((root / "publications").iterdir()) == publications
    rc, new, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc == 0 and new[-1]["status"] == "COMMITTED", (new, err)

    rc, started, err = begin(env, root, run_id, "manual", "MOCK_auto_to_manual_again")
    assert rc == 10, (started, err)
    set_mode(config, root, "manual")
    rc, finished, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_auto_to_manual_again")
    )
    assert rc == 0, (finished, err)
    from tests.unit.test_trading_calendar_mock_supplement_update import confirm_args

    rc, shown, err = command(
        env,
        *operation(root, old_review["run_id"], "mock-show", "--review-id", old_review["review_id"]),
    )
    assert rc == 0 and shown[-1]["review_policy"]["status"] == "STALE", (shown, err)

    rc, stale, err = command(env, *confirm_args(root, old_review))
    assert rc != 0 and stale[-1]["reason"] in {"REVIEW_STALE", "REVIEW_STALE_OR_CHANGED"}, (
        stale,
        err,
    )
    rc, observed, err = command(
        env, *policy(root, run_id, "show", "--operation-id", "MOCK_auto_to_manual")
    )
    assert rc == 0 and observed[-1]["status"] == "HISTORICAL_COMMITTED", (observed, err)


def test_policy_intent_write_unknown_recovered_by_original_id(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    run_id = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    pointer = (root / "CURRENT.json").read_bytes()
    folder = tmp_path / "unknown-write"
    folder.mkdir()
    injection = (
        OPEN_MOCK
        + """
from bt_api_py.trading_calendar_store import TradingCalendarStore
original_write = TradingCalendarStore._write_new
def uncertain(self, path, data):
    original_write(self, path, data)
    if path.name == 'POLICY_CHANGE_PENDING.json':
        raise OSError('injected after write')
TradingCalendarStore._write_new = uncertain
"""
    )
    rc, started, err = begin(
        env_without_network(folder, injection), root, run_id, "manual", "MOCK_unknown"
    )
    assert rc == 25 and started[-1]["status"] == "COMMIT_UNKNOWN", (started, err)
    rc, shown, err = command(env, *policy(root, run_id, "show", "--operation-id", "MOCK_unknown"))
    assert rc == 10 and shown[-1]["status"] == "PENDING_CONFIG", (shown, err)
    set_mode(config, root, "manual")
    rc, resumed, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_unknown")
    )
    assert rc == 0 and resumed[-1]["status"] == "COMMITTED", (resumed, err)
    assert (root / "CURRENT.json").read_bytes() == pointer


def test_partial_policy_record_write_recovers_same_operation(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    run_id = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    pointer = (root / "CURRENT.json").read_bytes()
    rc, started, err = begin(env, root, run_id, "manual", "MOCK_partial_record")
    assert rc == 10, (started, err)
    set_mode(config, root, "manual")
    folder = tmp_path / "partial-record"
    folder.mkdir()
    injection = (
        OPEN_MOCK
        + """
from bt_api_py.trading_calendar_store import TradingCalendarStore
original_write = TradingCalendarStore._write_new
def uncertain(self, path, data):
    original_write(self, path, data)
    if path.parent.name == 'policy_changes':
        raise OSError('injected after policy record write')
TradingCalendarStore._write_new = uncertain
"""
    )
    rc, result, err = command(
        env_without_network(folder, injection),
        *policy(root, run_id, "resume", "--operation-id", "MOCK_partial_record"),
    )
    assert rc == 25 and result[-1]["status"] == "COMMIT_UNKNOWN", (result, err)
    assert (root / "CURRENT.json").read_bytes() == pointer
    rc, pending, err = command(
        env, *policy(root, run_id, "show", "--operation-id", "MOCK_partial_record")
    )
    assert rc == 10 and pending[-1]["status"] == "PENDING_CONFIG", (pending, err)
    rc, recovered, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_partial_record")
    )
    assert rc == 0 and recovered[-1]["status"] == "COMMITTED", (recovered, err)
    assert (root / "CURRENT.json").read_bytes() == pointer


def test_publish_and_policy_begin_across_processes_do_not_cross(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    run_id = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    (root / "supplement.yaml").write_text(json.dumps(supplemental()))
    pointer = (root / "CURRENT.json").read_bytes()
    folder = tmp_path / "racing-update"
    folder.mkdir()
    ready, release = tmp_path / "ready", tmp_path / "release"
    injection = (
        OPEN_MOCK
        + f"""
from bt_api_py.trading_calendar_store import TradingCalendarStore
from pathlib import Path
import time
original_publish = TradingCalendarStore.publish
def wait_before_store_lock(self, *args, **kwargs):
    Path({str(ready)!r}).write_text('ready')
    deadline = time.monotonic() + 30
    while not Path({str(release)!r}).exists():
        if time.monotonic() >= deadline: raise TimeoutError('test coordination')
        time.sleep(0.01)
    return original_publish(self, *args, **kwargs)
TradingCalendarStore.publish = wait_before_store_lock
"""
    )
    update_args = operation(root, run_id, "mock-update")
    proc = subprocess.Popen(
        [sys.executable, "-m", "bt_api_py.trading_calendar_observation_run", *update_args],
        env=env_without_network(folder, injection),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 25
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "update did not reach the store publication boundary"
        rc, begin_result, err = begin(env, root, run_id, "manual", "MOCK_racing_switch")
        assert rc == 10 and begin_result[-1]["status"] == "PENDING_CONFIG", (begin_result, err)
    finally:
        release.write_text("release")
        stdout, stderr = proc.communicate(timeout=40)
    outcomes = [json.loads(line) for line in stdout.splitlines()]
    assert proc.returncode != 0 and outcomes[-1]["status"] != "COMMITTED", (outcomes, stderr)
    assert (root / "CURRENT.json").read_bytes() == pointer


def test_legacy_pending_review_cannot_survive_policy_roundtrip(tmp_path):
    from bt_api_py.trading_calendar_mock_cli import _retain_pending, allocate
    from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
    from tests.unit.test_trading_calendar_mock_supplement_update import confirm_args

    root, config = settings(tmp_path, "manual")
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 10 and lines[-1]["status"] == "PENDING_REVIEW", (lines, err)
    initial = lines[-1]
    operator = MockCalendarPublication(root=str(root), run_id=initial["run_id"])
    ids = allocate(candidate_id=initial["candidate_id"])
    saved = _retain_pending(operator, ids, initial["proposal"])
    assert saved["status"] == "COMMITTED"
    legacy = {
        "run_id": initial["run_id"],
        **ids,
        "review_payload_sha256": saved["review_payload_sha256"],
    }
    rc, _, _ = begin(env, root, initial["run_id"], "auto", "MOCK_legacy_to_auto")
    assert rc == 10
    set_mode(config, root, "auto")
    rc, _, _ = command(
        env, *policy(root, initial["run_id"], "resume", "--operation-id", "MOCK_legacy_to_auto")
    )
    assert rc == 0
    rc, shown, err = command(
        env, *operation(root, legacy["run_id"], "mock-show", "--review-id", legacy["review_id"])
    )
    assert rc == 10 and shown[-1]["review_policy"]["status"] == "STALE", (shown, err)
    rc, _, _ = begin(env, root, initial["run_id"], "manual", "MOCK_legacy_to_manual")
    assert rc == 10
    set_mode(config, root, "manual")
    rc, _, _ = command(
        env, *policy(root, initial["run_id"], "resume", "--operation-id", "MOCK_legacy_to_manual")
    )
    assert rc == 0
    rc, stale, err = command(env, *confirm_args(root, legacy))
    assert rc == 24 and stale[-1]["reason"] == "REVIEW_STALE_OR_CHANGED", (stale, err)
    assert not (root / "CURRENT.json").exists()


def test_mode_source_changes_are_audited_and_recovered(tmp_path):
    root, config = settings(tmp_path)
    set_mode(config, root, None)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    run_id = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    store = TradingCalendarStore(root)
    pointer = (root / "CURRENT.json").read_bytes()
    assert (store.read_review_policy().mode, store.read_review_policy().mode_source) == (
        "auto",
        "default",
    )
    set_mode(config, root, "auto")
    rc, refused, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc == 2 and refused[-1]["reason"] == "CONFIG_MODE_SOURCE_MISMATCH", (refused, err)
    assert refused[-1]["recovery"] == "RESTORE_POLICY_SOURCE_THEN_BEGIN_TRANSITION"
    assert (root / "CURRENT.json").read_bytes() == pointer
    rc, refused, err = begin(env, root, run_id, "manual", "MOCK_mismatch_source")
    assert rc == 24 and refused[-1]["reason"] == "CONFIG_MODE_SOURCE_MISMATCH", (refused, err)
    assert not (root / "POLICY_CHANGE_PENDING.json").exists()
    set_mode(config, root, None)
    rc, pending, err = begin(env, root, run_id, "auto", "MOCK_explicit_auto", "explicit")
    assert rc == 10 and pending[-1]["target_mode_source"] == "explicit", (pending, err)
    rc, mismatch, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_explicit_auto")
    )
    assert rc == 25 and mismatch[-1]["config_reason"] == "CONFIG_TARGET_MISMATCH", (mismatch, err)
    set_mode(config, root, "auto")
    rc, done, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_explicit_auto")
    )
    assert rc == 0 and done[-1]["policy"]["mode_source"] == "explicit", (done, err)
    rc, pending, err = begin(env, root, run_id, "auto", "MOCK_default_auto", "default")
    assert rc == 10, (pending, err)
    set_mode(config, root, None)
    rc, done, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_default_auto")
    )
    assert rc == 0 and done[-1]["policy"]["mode_source"] == "default", (done, err)
    rc, pending, err = begin(env, root, run_id, "manual", "MOCK_to_manual")
    assert rc == 10, (pending, err)
    set_mode(config, root, "manual")
    rc, done, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_to_manual")
    )
    assert rc == 0 and done[-1]["policy"]["mode_source"] == "explicit", (done, err)
    rc, pending, err = begin(env, root, run_id, "auto", "MOCK_manual_default", "default")
    assert rc == 10 and pending[-1]["target_mode_source"] == "default", (pending, err)
    set_mode(config, root, None)
    folder = tmp_path / "unknown-default"
    folder.mkdir()
    injection = (
        OPEN_MOCK
        + """
from bt_api_py.trading_calendar_store import TradingCalendarStore
original_write = TradingCalendarStore._write_new
def uncertain(self, path, data):
    original_write(self, path, data)
    if path.parent.name == 'policy_changes': raise OSError('injected after record')
TradingCalendarStore._write_new = uncertain
"""
    )
    rc, unknown, err = command(
        env_without_network(folder, injection),
        *policy(root, run_id, "resume", "--operation-id", "MOCK_manual_default"),
    )
    assert rc == 25 and unknown[-1]["status"] == "COMMIT_UNKNOWN", (unknown, err)
    rc, shown, err = command(
        env, *policy(root, run_id, "show", "--operation-id", "MOCK_manual_default")
    )
    assert rc == 10 and shown[-1]["target_mode_source"] == "default", (shown, err)
    rc, recovered, err = command(
        env, *policy(root, run_id, "resume", "--operation-id", "MOCK_manual_default")
    )
    assert rc == 0 and recovered[-1]["policy"]["mode_source"] == "default", (recovered, err)
    record = store.read_policy_change("MOCK_manual_default")
    assert record["schema"] == "bt_api.calendar-store.policy-change/2"
    assert record["new_mode_source"] == "default"
    assert (root / "CURRENT.json").read_bytes() == pointer


def test_empty_operation_id_is_rejected_without_random_replacement(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    run_id = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    for action in ("begin", "resume", "show"):
        extra = (
            (
                "--mode",
                "manual",
                "--actor",
                "MOCK_reviewer",
                "--at-utc",
                "2026-11-13T00:00:00.000000Z",
            )
            if action == "begin"
            else ()
        )
        rc, result, err = command(env, *policy(root, run_id, action, "--operation-id", "", *extra))
        assert rc == 2 and all(row.get("event") != "MOCK_POLICY_ID_ALLOCATED" for row in result), (
            result,
            err,
        )
        assert not (root / "POLICY_CHANGE_PENDING.json").exists()


def test_missing_or_invalid_config_before_begin_and_update_is_diagnostic(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    run_id = next(row["run_id"] for row in lines if row.get("candidate_state") == "COMMITTED")
    pointer = (root / "CURRENT.json").read_bytes()
    config.unlink()
    for scenario, expected in (
        ("missing", "CONFIG_READ_FAILED"),
        ("invalid", "CONFIG_YAML_INVALID"),
    ):
        if scenario == "invalid":
            config.write_text("private_config_value: [broken")
        operation_id = "MOCK_diagnostic_" + scenario
        rc, started, err = begin(env, root, run_id, "manual", operation_id)
        assert rc == 24 and started[-1]["status"] == "NOT_COMMITTED", (started, err)
        assert started[-1]["reason"] == "SIMULATION_CONFIG_UNAVAILABLE"
        assert started[-1]["config_reason"] == expected
        assert started[-1]["operation_id"] == operation_id
        assert str(config) not in json.dumps(started) and "private_config_value" not in json.dumps(
            started
        )
        rc, updated, err = command(env, *operation(root, run_id, "mock-update"))
        assert rc == 2 and updated[-1]["reason"] == "SIMULATION_CONFIG_UNAVAILABLE", (updated, err)
        assert updated[-1]["config_reason"] == expected
        assert str(config) not in json.dumps(updated) and "private_config_value" not in json.dumps(
            updated
        )
        assert not (root / "POLICY_CHANGE_PENDING.json").exists()
        assert (root / "CURRENT.json").read_bytes() == pointer
