"""Offline persistence and recovery of simulated per-scope check history."""

import fcntl
import json
import subprocess
import sys

import pytest

from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root, scope
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation


def batch_args(root, run_id):
    return operation(
        root,
        run_id,
        "mock-batch",
        "--scope-json",
        json.dumps(scope("SHFE")),
        "--scope-json",
        json.dumps(scope("DCE")),
    )


_FAIL_HEAD = """
_original_scope_replace = os.replace
def fail_scope_replace(src, dst, *args, **kwargs):
    if dst == 'scope-check-head.json' and os.environ.get('SCOPE_HEAD_FAILURE'):
        if os.environ['SCOPE_HEAD_FAILURE'] == 'AFTER':
            _original_scope_replace(src, dst, *args, **kwargs)
        raise OSError('injected scope head failure')
    return _original_scope_replace(src, dst, *args, **kwargs)
os.replace = fail_scope_replace
"""

_FAIL_DIRECTORY_FSYNC = """
import stat
_scope_real_replace, _scope_real_fsync = os.replace, os.fsync
_scope_pending_fsync = False
def scope_replace(src, dst, *args, **kwargs):
    global _scope_pending_fsync
    value = _scope_real_replace(src, dst, *args, **kwargs)
    if dst == 'scope-check-head.json' and os.environ.get('FAIL_SCOPE_DIR_FSYNC'):
        _scope_pending_fsync = True
    return value
def scope_fsync(fd):
    global _scope_pending_fsync
    if _scope_pending_fsync and stat.S_ISDIR(os.fstat(fd).st_mode):
        _scope_pending_fsync = False
        raise OSError('injected directory fsync failure')
    return _scope_real_fsync(fd)
os.replace, os.fsync = scope_replace, scope_fsync
"""


def test_directory_fsync_failure_is_unknown_after_visible_head(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto", _FAIL_DIRECTORY_FSYNC)
    env["FAIL_SCOPE_DIR_FSYNC"] = "1"
    before = (root / "CURRENT.json").read_bytes()
    rc, rows, err = command(env, *batch_args(root, run_id))
    assert rc != 0 and rows[-1]["reason"] == "SCOPE_CHECK_RECONCILE_FIRST", (rows, err)
    assert rows[-1]["status"] == "UNKNOWN" and (root / "CURRENT.json").read_bytes() == before
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", rows[-1]["batch_id"])
    )
    assert rc == 0 and shown[-1]["scope_checks"]["status"] == "VISIBLE_UNAUTHENTICATED", (
        shown,
        err,
    )


@pytest.mark.parametrize("where", ["BEFORE", "AFTER"])
def test_scope_head_write_unknown_retains_original_batch_for_readback(tmp_path, where):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto", _FAIL_HEAD)
    before = (root / "CURRENT.json").read_bytes()
    env["SCOPE_HEAD_FAILURE"] = where
    rc, rows, err = command(env, *batch_args(root, run_id))
    assert rc != 0 and rows[-1]["status"] == "UNKNOWN", (rows, err)
    result = rows[-1]
    assert result["reason"] == "SCOPE_CHECK_RECONCILE_FIRST"
    assert (root / "CURRENT.json").read_bytes() == before
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", result["batch_id"])
    )
    report = shown[-1]["scope_checks"]
    if where == "AFTER":
        assert report["status"] == "VISIBLE_UNAUTHENTICATED", (shown, err)
        assert all(
            row["last_success"]["status"] == "VISIBLE_UNAUTHENTICATED" for row in report["scopes"]
        )
    else:
        assert rc != 0 and shown[-1]["status"] == "UNKNOWN" and report["status"] == "UNKNOWN", (
            shown,
            err,
        )
        assert report["original_record"]["status"] == "VISIBLE_UNAUTHENTICATED"


def test_corrupted_check_record_fails_closed_on_restart(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    rc, rows, err = command(env, *batch_args(root, run_id))
    assert rc == 0, (rows, err)
    result = rows[-1]
    path = root / "observation-runs" / f"scope-check-batch-{result['batch_id']}.json"
    path.write_text("{}")
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", result["batch_id"])
    )
    assert (
        rc != 0
        and shown[-1]["status"] == "UNKNOWN"
        and shown[-1]["scope_checks"]["status"] == "UNKNOWN"
    ), (shown, err)
    before = (root / "CURRENT.json").read_bytes()
    rc, attempted, err = command(env, *batch_args(root, run_id))
    assert rc != 0 and attempted[-1]["status"] == "UNKNOWN", (attempted, err)
    assert (root / "CURRENT.json").read_bytes() == before


def test_local_sequence_not_unverified_wall_clock_sets_order():
    from bt_api_py import trading_calendar_scope_checks as checks

    target = scope("SHFE")["target"]

    def record(sequence, timestamp):
        return (
            {
                "batch_id": str(sequence),
                "sequence": sequence,
                "entries": [
                    {
                        "target": target,
                        "observation_id": str(sequence),
                        "check_status": "SUCCESS",
                        "success_proof": {
                            "declared_utc": timestamp,
                            "requested_contract": "RB2701",
                            "baseline_publication_id": "baseline",
                            "baseline_version_sha256": "digest",
                        },
                    }
                ],
            },
            "unused",
        )

    result = checks._result(
        [record(1, "2026-11-13T09:00:00+00:00"), record(2, "2020-01-01T00:00:00+00:00")],
        [{"target": target, "requested_contract": "RB2701"}],
    )
    latest = result["scopes"][0]["last_success"]
    assert latest["batch_id"] == "2" and latest["clock"] == "CLOCK_UNVERIFIED"
    assert latest["ordering"] == "COORDINATED_LOCAL_RECORD_SEQUENCE"


def test_original_batch_id_cannot_be_rewritten_and_legacy_is_unknown(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    rc, rows, err = command(env, *batch_args(root, run_id))
    assert rc == 0, (rows, err)
    result = rows[-1]
    head = root / "observation-runs" / "scope-check-head.json"
    original_head = head.read_bytes()
    script = """import json, sys
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from bt_api_py.trading_calendar_scope_checks import record
operator = MockCalendarPublication(root=sys.argv[1], run_id=sys.argv[2])
print(json.dumps(record(operator=operator, batch_id=sys.argv[3], evidence_sha256=sys.argv[4])))
"""
    repeated = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(root),
            result["run_id"],
            result["batch_id"],
            result["batch_evidence_sha256"],
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["reason"] == "ORIGINAL_BATCH_RECONCILE_FIRST"
    assert head.read_bytes() == original_head
    waiting_script = script.replace(
        "print(json.dumps", "print('READY', flush=True); sys.stdin.readline(); print(json.dumps"
    )
    busy = subprocess.Popen(
        [
            sys.executable,
            "-c",
            waiting_script,
            str(root),
            result["run_id"],
            result["batch_id"],
            result["batch_evidence_sha256"],
        ],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert busy.stdout.readline().strip() == "READY"
    with (root / ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        out, error = busy.communicate("go\n", timeout=60)
        assert busy.returncode == 0, error
        assert json.loads(out)["reason"] == "SCOPE_CHECK_RECONCILE_FIRST"
    assert head.read_bytes() == original_head
    (root / "observation-runs" / f"scope-check-batch-{result['batch_id']}.json").unlink()
    head.unlink()
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", result["batch_id"])
    )
    assert shown[-1]["evidence"]["batch_id"] == result["batch_id"], (shown, err)
    assert shown[-1]["scope_checks"]["reason"] == "LEGACY_OR_UNRECORDED_BATCH"
    assert rc != 0 and shown[-1]["status"] == "UNKNOWN"


def test_distinct_requested_contracts_keep_separate_successes(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    env["FAIL_DCE"] = "1"
    rc, first, err = command(env, *batch_args(root, run_id))
    assert rc == 0, (first, err)
    old = first[-1]
    next_scope = scope("SHFE")
    next_scope["contract"] = "RB2705"
    args = operation(
        root,
        run_id,
        "mock-batch",
        "--scope-json",
        json.dumps(next_scope),
        "--scope-json",
        json.dumps(scope("DCE")),
    )
    rc, second, err = command(env, *args)
    assert second[-1]["status"] in {"COMMITTED", "SAME"}, (second, err)
    new = second[-1]
    for batch, expected in ((old, "RB2701"), (new, "RB2705")):
        rc, shown, err = command(
            env, *operation(root, run_id, "mock-batch-show", "--batch-id", batch["batch_id"])
        )
        assert rc == 0, (shown, err)
        checks = shown[-1]["scope_checks"]["scopes"]
        item = next(row for row in checks if row["target"]["venue"] == "SHFE")
        assert item["requested_contract"] == expected
        assert item["last_success"]["observation_id"] == batch["observations"][0]["observation_id"]


def test_legacy_scope_record_rebuilds_contract_only_from_verified_proof(tmp_path):
    from bt_api_py import trading_calendar_detail_collection as storage

    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    rc, rows, err = command(env, *batch_args(root, run_id))
    assert rc == 0, (rows, err)
    result = rows[-1]
    directory = root / "observation-runs"
    record_path = directory / f"scope-check-batch-{result['batch_id']}.json"
    head_path = directory / "scope-check-head.json"
    record = json.loads(record_path.read_bytes())["body"]
    record["schema"] = "bt_api.calendar-scope-check-record/1"
    for entry in record["entries"]:
        entry.pop("requested_contract")

    def install(body):
        encoded = storage._envelope(body)
        record_path.write_bytes(encoded)
        head = json.loads(head_path.read_bytes())["body"]
        head["record_sha256"] = storage._unwrap(encoded)[1]
        head_path.write_bytes(storage._envelope(head))

    install(record)
    args = operation(root, run_id, "mock-batch-show", "--batch-id", result["batch_id"])
    rc, shown, err = command(env, *args)
    assert rc == 0 and shown[-1]["scope_checks"]["scopes"][0]["requested_contract"] == "RB2701", (
        shown,
        err,
    )
    record["entries"][0]["success_proof"].pop("requested_contract")
    install(record)
    rc, shown, err = command(env, *args)
    assert rc != 0 and shown[-1]["status"] == "UNKNOWN", (shown, err)
