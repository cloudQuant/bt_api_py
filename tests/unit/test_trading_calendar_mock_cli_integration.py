"""CLI to real local store using a small, fictional, network-disabled dataset."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tests.unit.test_trading_calendar_observation_run import env_without_network

SMALL_MOCK = """
from tests.unit.test_trading_calendar_review_bundle import sample
from bt_api_py import trading_calendar_mock as mock

def tiny_mock(year):
    data = sample(days=2)
    data["metadata"] = mock.LABEL.copy()
    data["source_texts"] = {mock.SOURCE["id"]: "MOCK fictional schedules; SIMULATION_ONLY"}
    for shard in data["shards"]:
        layers = shard["layers"]
        layers["sources"] = [mock.SOURCE.copy()]
        for day in layers["trading_days"]:
            day["source_ids"] = [mock.SOURCE["id"]]
        for row in layers["trading_hours"]:
            row["source_ids"] = [mock.SOURCE["id"]]
            if row["kind"] == "future":
                row["day_windows"].append({"id": "afternoon", "phase": "continuous", "start": "13:30", "end": "15:00", "end_day_offset": 0})
    supplement = {"sources": [mock.SOURCE.copy()], "trading_days": [data["shards"][0]["layers"]["trading_days"][0].copy()], "trading_hours": [], "exceptions": []}
    return data, supplement
mock.build = tiny_mock
"""


def command(env, *args):
    result = subprocess.run(
        [sys.executable, "-m", "bt_api_py.trading_calendar_observation_run", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    values = [json.loads(line) for line in result.stdout.splitlines()]
    return result.returncode, values, result.stderr


def test_manual_cli_real_store_pending_confirm_update_and_no_replay(tmp_path):
    env = env_without_network(tmp_path, SMALL_MOCK)
    rc, lines, stderr = command(
        env,
        "run",
        "--mock",
        "--mock-year",
        "2026",
        "--mock-review-mode",
        "manual",
        "--budget-seconds",
        "20",
        "--trusted-local-posix",
    )
    assert rc == 10, (lines, stderr)
    run = next(item for item in lines if item.get("candidate_state") == "COMMITTED")
    pending = lines[-1]
    assert pending["status"] == "PENDING_REVIEW", pending
    root, run_id = run["simulation_root"], run["run_id"]
    try:
        show_args = (
            "mock-show",
            "--calendar-root",
            root,
            "--run-id",
            run_id,
            "--review-id",
            pending["review_id"],
            "--trusted-local-posix",
        )
        rc, shown, stderr = command(env, *show_args)
        assert rc == 10, (shown, stderr)
        assert shown[-1]["current"]["status"] == "UNKNOWN"
        assert shown[-1]["pending_review"]["status"] == "VISIBLE_UNAUTHENTICATED"
        confirm = (
            "mock-confirm",
            "--calendar-root",
            root,
            "--run-id",
            run_id,
            "--review-id",
            pending["review_id"],
            "--review-sha256",
            pending["review_payload_sha256"],
            "--reviewer",
            "MOCK_reviewer",
            "--confirmed-at",
            "2026-11-13T00:00:00.000000Z",
            "--trusted-local-posix",
        )
        rc, confirmed, stderr = command(env, *confirm)
        assert rc == 0, (confirmed, stderr)
        assert (
            confirmed[-1]["status"] == "COMMITTED"
            and confirmed[-1]["current_visibility"] == "VISIBLE"
        )
        rc, visible, stderr = command(env, *show_args)
        assert rc == 0 and visible[-1]["current"]["status"] == "VISIBLE_UNAUTHENTICATED", (
            visible,
            stderr,
        )
        current_path = Path(root) / "CURRENT.json"
        original_current = current_path.read_bytes()
        try:
            current_path.write_bytes(b"invalid mock current")
            rc, damaged, stderr = command(env, *show_args)
            assert rc == 10 and damaged[-1]["current"]["status"] == "UNKNOWN", (damaged, stderr)
        finally:
            current_path.write_bytes(original_current)
        rc, replay, stderr = command(env, *confirm)
        assert rc == 25 and replay[-1]["reason"] == "ORIGINAL_IDS_RECONCILE_FIRST", (replay, stderr)
        rc, unchanged, stderr = command(
            env,
            "mock-update",
            "--calendar-root",
            root,
            "--run-id",
            run_id,
            "--variant",
            "BASE",
            "--trusted-local-posix",
        )
        assert rc == 10 and unchanged[-1]["status"] == "SAME", repr((unchanged, stderr))
        rc, changed, stderr = command(
            env,
            "mock-update",
            "--calendar-root",
            root,
            "--run-id",
            run_id,
            "--variant",
            "AFTERNOON_CHANGE",
            "--trusted-local-posix",
        )
        assert rc == 10 and changed[-1]["status"] == "PENDING_REVIEW", (changed, stderr)
        assert changed[-1]["proposal"]["status"] == "CHANGED"
        assert changed[-1]["proposal"]["difference"]["changes"]["trading_hours"]
        first_pending = changed[-1]
        rc, second, stderr = command(
            env,
            "mock-update",
            "--calendar-root",
            root,
            "--run-id",
            run_id,
            "--variant",
            "AFTERNOON_CHANGE",
            "--trusted-local-posix",
        )
        assert rc == 10 and second[-1]["status"] == "PENDING_REVIEW", (second, stderr)
        second_pending = second[-1]

        def review_args(item):
            return (
                "mock-confirm",
                "--calendar-root",
                root,
                "--run-id",
                run_id,
                "--review-id",
                item["review_id"],
                "--review-sha256",
                item["review_payload_sha256"],
                "--reviewer",
                "MOCK_reviewer",
                "--confirmed-at",
                "2026-11-13T00:00:00.000000Z",
                "--trusted-local-posix",
            )

        rc, published, stderr = command(env, *review_args(second_pending))
        assert rc == 0 and published[-1]["status"] == "COMMITTED", (published, stderr)
        rc, stale, stderr = command(env, *review_args(first_pending))
        assert rc == 24 and stale[-1]["reason"] == "REVIEW_STALE_OR_CHANGED", (stale, stderr)
    finally:
        if os.path.isdir(root):
            shutil.rmtree(root)


def test_auto_cli_real_store_publishes_then_records_same(tmp_path):
    env = env_without_network(tmp_path, SMALL_MOCK)
    rc, lines, stderr = command(
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
    assert rc == 0, (lines, stderr)
    run = next(item for item in lines if item.get("candidate_state") == "COMMITTED")
    assert lines[-1]["status"] == "COMMITTED" and lines[-1]["current_visibility"] == "VISIBLE"
    root, run_id = run["simulation_root"], run["run_id"]
    try:
        rc, same, stderr = command(
            env,
            "mock-update",
            "--calendar-root",
            root,
            "--run-id",
            run_id,
            "--variant",
            "BASE",
            "--trusted-local-posix",
        )
        assert rc == 10 and same[-1]["status"] == "SAME", repr((same, stderr))
        rc, changed, stderr = command(
            env,
            "mock-update",
            "--calendar-root",
            root,
            "--run-id",
            run_id,
            "--variant",
            "AFTERNOON_CHANGE",
            "--trusted-local-posix",
        )
        assert rc == 0 and changed[-1]["status"] == "COMMITTED", (changed, stderr)
    finally:
        if os.path.isdir(root):
            shutil.rmtree(root)
