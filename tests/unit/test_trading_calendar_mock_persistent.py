"""Offline CLI restart checks for explicitly configured simulation roots."""

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tests.unit.test_trading_calendar_mock_cli_integration import SMALL_MOCK, command
from tests.unit.test_trading_calendar_observation_run import env_without_network


def settings(tmp_path, mode="auto"):
    base = Path(os.path.realpath(tmp_path))
    root = base / "calendar"
    root.mkdir(mode=0o700)
    config = base / "config.json"
    config.write_text(json.dumps({"calendars": {"root": str(root), "review_mode": mode}}))
    return root, config


def run_args(root, config, *extra):
    return (
        "run",
        "--mock",
        "--mock-year",
        "2026",
        "--calendar-root",
        str(root),
        "--config",
        str(config),
        "--budget-seconds",
        "20",
        "--trusted-local-posix",
        *extra,
    )


def operation(root, run_id, name, *extra):
    return (name, "--calendar-root", str(root), "--run-id", run_id, "--trusted-local-posix", *extra)


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_persistent_restart_review_and_update(tmp_path, mode):
    root, config = settings(tmp_path, mode)
    env = env_without_network(tmp_path, SMALL_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == (0 if mode == "auto" else 10), (lines, err)
    allocation = next(i for i, row in enumerate(lines) if row.get("event") == "MOCK_IDS_ALLOCATED")
    created = next(i for i, row in enumerate(lines) if row.get("event") == "MOCK_ROOT_CREATED")
    pub_ids = next(
        i for i, row in enumerate(lines) if row.get("event") == "MOCK_PUBLICATION_IDS_ALLOCATED"
    )
    assert allocation < created and pub_ids < created
    capture = next(row for row in lines if row.get("candidate_state") == "COMMITTED")
    assert capture["simulation_root"] == str(root)
    assert capture["supplement_input"] == "SYNTHETIC_MOCK_NOT_USER_YAML"
    assert (root / "MOCK-SIMULATION_ONLY.json").exists()
    run_id, outcome = capture["run_id"], lines[-1]
    if mode == "manual":
        assert outcome["status"] == "PENDING_REVIEW"
        rc, confirmed, err = command(
            env,
            *operation(
                root,
                run_id,
                "mock-confirm",
                "--review-id",
                outcome["review_id"],
                "--review-sha256",
                outcome["review_payload_sha256"],
                "--reviewer",
                "MOCK_reviewer",
                "--confirmed-at",
                "2026-11-13T00:00:00.000000Z",
            ),
        )
        assert rc == 0 and confirmed[-1]["status"] == "COMMITTED", (confirmed, err)
    rc, shown, err = command(env, *operation(root, run_id, "mock-show"))
    assert rc == 0 and shown[-1]["current"]["status"] == "VISIBLE_UNAUTHENTICATED", (shown, err)
    rc, same, err = command(env, *operation(root, run_id, "mock-update"))
    assert rc == 10 and same[-1]["status"] == "SAME", (same, err)
    rc, changed, err = command(
        env, *operation(root, run_id, "mock-update", "--variant", "AFTERNOON_CHANGE")
    )
    assert rc == (0 if mode == "auto" else 10), (changed, err)
    assert changed[-1]["status"] == ("COMMITTED" if mode == "auto" else "PENDING_REVIEW")
    if mode == "manual":
        pending = changed[-1]
        rc, confirmed, err = command(
            env,
            *operation(
                root,
                run_id,
                "mock-confirm",
                "--review-id",
                pending["review_id"],
                "--review-sha256",
                pending["review_payload_sha256"],
                "--reviewer",
                "MOCK_reviewer",
                "--confirmed-at",
                "2026-11-13T00:00:00.000000Z",
            ),
        )
        assert rc == 0 and confirmed[-1]["status"] == "COMMITTED", (confirmed, err)
    config.write_text(
        json.dumps(
            {
                "calendars": {
                    "root": str(root),
                    "review_mode": "manual" if mode == "auto" else "auto",
                }
            }
        )
    )
    rc, error, _ = command(env, *operation(root, run_id, "mock-update"))
    assert rc != 0 and error[-1]["reason"] == "SIMULATION_ROOT_OR_CONFIG_OR_POLICY_CONFLICT"


@pytest.mark.parametrize("scenario", ["existing", "mismatch", "mode", "nonprivate", "symlink"])
def test_refuses_unsafe_explicit_root(tmp_path, scenario):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, SMALL_MOCK)
    extra = ()
    if scenario == "existing":
        (root / "CURRENT.json").write_text("ordinary calendar")
    elif scenario == "mismatch":
        config.write_text(json.dumps({"calendars": {"root": str(root.parent)}}))
    elif scenario == "mode":
        extra = ("--mock-review-mode", "manual")
    elif scenario == "nonprivate":
        root.chmod(0o755)
    else:
        link = root.parent / "linked"
        link.symlink_to(root, target_is_directory=True)
        root = link
    rc, _, _ = command(env, *run_args(root, config, *extra))
    assert rc != 0
    assert not (root / "MOCK-SIMULATION_ONLY.json").exists()
    if scenario == "existing":
        assert (root / "CURRENT.json").read_text() == "ordinary calendar"


def test_unknown_declaration_preserves_original_ids(tmp_path, monkeypatch):
    from bt_api_py import trading_calendar_detail_collection as storage
    from bt_api_py import trading_calendar_mock as mock
    from bt_api_py import trading_calendar_observation_run as runs

    root, config = settings(tmp_path)
    original_write = storage._write

    def uncertain(fd, chain, name, data, limit):
        result = original_write(fd, chain, name, data, limit)
        return "COMMIT_UNKNOWN" if name == "MOCK-SIMULATION_ONLY.json" else result

    monkeypatch.setattr(storage, "_write", uncertain)
    monkeypatch.setattr(mock, "build", lambda year: ({}, {}))
    ids = [uuid.uuid4().hex for _ in range(4)]
    events = []
    result = runs.run_once(
        root=str(root),
        config=str(config),
        supplement=None,
        budget_seconds=20,
        enable_shfe=False,
        filesystem=runs.TRUSTED_LOCAL_POSIX,
        run_id=ids[0],
        capture_id=ids[1],
        parent_id=ids[2],
        derived_id=ids[3],
        mock_year=2026,
        emit=events.append,
    )
    assert result["state"] == "COMMIT_UNKNOWN"
    assert [result[key] for key in ("run_id", "capture_id", "parent_id", "derived_id")] == ids
    assert events[0]["event"] == "MOCK_IDS_ALLOCATED"
    assert (root / "MOCK-SIMULATION_ONLY.json").exists()
    assert not (root / "CURRENT.json").exists()


def test_persistent_policy_mode_change_rejected_before_claim_and_restart(tmp_path):
    from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication

    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, SMALL_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    run = next(row for row in lines if row.get("candidate_state") == "COMMITTED")
    operator = MockCalendarPublication(root=str(root), run_id=run["run_id"])
    policy = operator._store.read_review_policy()
    names_before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    protected = [config, root / "CURRENT.json", root / "REVIEW_POLICY.json"]
    contents_before = [path.read_bytes() for path in protected]
    events = []
    result = operator.change_policy(
        expected_revision=policy.revision,
        mode="manual",
        operation_id="MOCK_unsupported_policy_change",
        actor="MOCK_reviewer",
        at_utc="2026-11-13T00:00:00.000000Z",
        emit=events.append,
    )
    assert result["status"] == "NOT_COMMITTED"
    assert result["reason"] == "POLICY_CHANGE_NOT_SUPPORTED"
    assert not events
    assert operator._store.read_review_policy() == policy
    assert [path.read_bytes() for path in protected] == contents_before
    assert sorted(str(path.relative_to(root)) for path in root.rglob("*")) == names_before
    assert (
        operator.inspect_policy_claim(operation_id="MOCK_unsupported_policy_change")["status"]
        == "NOT_OBSERVED"
    )
    rc, shown, err = command(env, *operation(root, run["run_id"], "mock-show"))
    assert rc == 0 and shown[-1]["current"]["status"] == "VISIBLE_UNAUTHENTICATED", (shown, err)
    rc, updated, err = command(env, *operation(root, run["run_id"], "mock-update"))
    assert rc == 10 and updated[-1]["status"] == "SAME", (updated, err)


def _minimal_attempt(root, config, monkeypatch):
    from bt_api_py import trading_calendar_mock as mock
    from bt_api_py import trading_calendar_observation_run as runs

    monkeypatch.setattr(mock, "build", lambda year: ({}, {}))
    ids = [uuid.uuid4().hex for _ in range(4)]
    return runs.run_once(
        root=str(root),
        config=str(config),
        supplement=None,
        budget_seconds=20,
        enable_shfe=False,
        filesystem=runs.TRUSTED_LOCAL_POSIX,
        run_id=ids[0],
        capture_id=ids[1],
        parent_id=ids[2],
        derived_id=ids[3],
        mock_year=2026,
        emit=lambda _: None,
    )


def test_busy_cooperating_writer_prevents_declaration(tmp_path, monkeypatch):
    root, config = settings(tmp_path)
    code = (
        "import fcntl, os, sys; "
        "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600); "
        "fcntl.flock(fd, fcntl.LOCK_EX); print('locked', flush=True); sys.stdin.readline()"
    )
    with subprocess.Popen(
        [sys.executable, "-c", code, str(root / ".lock")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout.readline().strip() == "locked"
            result = _minimal_attempt(root, config, monkeypatch)
            assert result["state"] == "NOT_COMMITTED"
            assert not (root / "MOCK-SIMULATION_ONLY.json").exists()
        finally:
            child.stdin.write("\n")
            child.stdin.flush()
    assert _minimal_attempt(root, config, monkeypatch)["state"] == "NOT_COMMITTED"
    assert not (root / "MOCK-SIMULATION_ONLY.json").exists()


@pytest.mark.parametrize("when", ["before", "after"])
def test_concurrent_current_aborts_declaration(tmp_path, monkeypatch, when):
    from bt_api_py import trading_calendar_detail_collection as storage
    from bt_api_py import trading_calendar_preview_reports as preview_reports

    root, config = settings(tmp_path)
    original_check = preview_reports._directory_matches
    original_write = storage._write

    def add_before(chain):
        (root / "CURRENT.json").write_text("ordinary current")
        return original_check(chain)

    def add_after(fd, chain, name, data, limit):
        result = original_write(fd, chain, name, data, limit)
        if name == "MOCK-SIMULATION_ONLY.json":
            (root / "CURRENT.json").write_text("ordinary current")
        return result

    if when == "before":
        monkeypatch.setattr(preview_reports, "_directory_matches", add_before)
    else:
        monkeypatch.setattr(storage, "_write", add_after)
    result = _minimal_attempt(root, config, monkeypatch)
    assert result["state"] == ("NOT_COMMITTED" if when == "before" else "COMMIT_UNKNOWN")
    assert (root / "CURRENT.json").read_text() == "ordinary current"
    assert (root / "MOCK-SIMULATION_ONLY.json").exists() == (when == "after")
    assert not (root / "observation-runs").exists()


@pytest.mark.parametrize("entry", ["CURRENT.json", "versions/ordinary.json"])
def test_store_initialization_rejects_late_nonmock_data(tmp_path, entry):
    root, config = settings(tmp_path)
    injection = (
        SMALL_MOCK
        + f"""
from bt_api_py.trading_calendar_store import TradingCalendarStore
original_initialize = TradingCalendarStore.initialize_review_policy
def insert_before_lock(self, **kwargs):
    if kwargs.get("require_empty_store"):
        target = self.root / {entry!r}
        target.parent.mkdir(mode=0o700, exist_ok=True)
        target.write_text("ordinary data")
    return original_initialize(self, **kwargs)
TradingCalendarStore.initialize_review_policy = insert_before_lock
"""
    )
    env = env_without_network(tmp_path, injection)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc != 0 and lines[-1]["status"] == "UNKNOWN", (lines, err)
    assert (root / entry).read_text() == "ordinary data"
    assert not (root / "REVIEW_POLICY_INITIALIZED.json").exists()
    assert not (root / "REVIEW_POLICY.json").exists()


def test_legacy_temporary_root_can_still_change_policy(tmp_path):
    from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication

    env = env_without_network(tmp_path, SMALL_MOCK)
    rc, lines, err = command(
        env,
        "run",
        "--mock",
        "--mock-year",
        "2026",
        "--mock-review-mode",
        "auto",
        "--budget-seconds",
        "20",
        "--trusted-local-posix",
    )
    assert rc == 0, (lines, err)
    run = next(row for row in lines if row.get("candidate_state") == "COMMITTED")
    try:
        operator = MockCalendarPublication(root=run["simulation_root"], run_id=run["run_id"])
        policy = operator._store.read_review_policy()
        result = operator.change_policy(
            expected_revision=policy.revision,
            mode="manual",
            operation_id="MOCK_policy_change",
            actor="MOCK_reviewer",
            at_utc="2026-11-13T00:00:00.000000Z",
            emit=lambda _: None,
        )
        assert result["status"] == "COMMITTED"
        assert operator._store.read_review_policy().mode == "manual"
    finally:
        shutil.rmtree(run["simulation_root"])


def test_missing_fcntl_imports_and_rejects_before_writing(tmp_path):
    code = """
import builtins, json, sys
original_import = builtins.__import__
def no_fcntl(name, *args, **kwargs):
    if name == 'fcntl':
        raise ModuleNotFoundError('fcntl unavailable')
    return original_import(name, *args, **kwargs)
builtins.__import__ = no_fcntl
from bt_api_py.trading_calendar_mock_run import run_mock
result = run_mock(root=sys.argv[1], config=sys.argv[2], supplement=None,
    budget_seconds=20, enable_shfe=False, filesystem='TRUSTED_LOCAL_POSIX',
    run_id='1' * 32, candidate_id='', fetch_product_navigation=False,
    ine_evidence_ids=None, ine_retry_config=None, capture_id='2' * 32,
    parent_id='3' * 32, parent_sha256='', derived_id='4' * 32,
    source_texts=None, decisions=None, year=2026,
    emit=lambda _: (_ for _ in ()).throw(AssertionError('must not emit')))
print(json.dumps(result))
"""
    root = tmp_path / "must-not-exist"
    result = subprocess.run(
        [sys.executable, "-c", code, str(root), str(tmp_path / "config")],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["state"] == "NOT_ATTEMPTED"
    assert observed["reason"] == "UNSUPPORTED_FILESYSTEM"
    assert not root.exists()
