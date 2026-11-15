"""Explicit AKShare plus mock run; all source calls use an offline substitute."""

import json
import os
import shutil
import subprocess
import sys

from tests.unit.test_trading_calendar_mock_cli_integration import SMALL_MOCK
from tests.unit.test_trading_calendar_observation_run import env_without_network

OFFLINE = (
    SMALL_MOCK
    + """
import os
from bt_api_py import trading_calendar_akshare_observation as obs
from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract
_old_build = mock.build

def small_with_fictional_contract(year):
    data, supplement = _old_build(year)
    for shard in data["shards"]:
        for row in shard["layers"]["trading_hours"]:
            if row["kind"] == "future":
                old = row["contracts"][0]
                new = "mock_" + shard["venue"].lower() + "_future_contract"
                row["contracts"] = [new]
                row["night_windows"] = [{"id": "night", "phase": "continuous", "start": "21:00", "end": "01:00", "end_day_offset": 1}]
                for req in shard["requirements"]:
                    if req["contract"] == old:
                        req["contract"] = new
    return data, supplement
mock.build = small_with_fictional_contract
_original_observe = obs.observe

def fake_observe(**kwargs):
    status = os.environ.get("AKSHARE_FAKE", "FORBID")
    if status == "FORBID":
        raise AssertionError("unexpected source request")
    if status == "ATTEMPT_UNKNOWN":
        def uncertain(**values): raise RuntimeError("offline uncertain collector")
        return _original_observe(**kwargs, _collector=uncertain)
    rows = [["交易时间", "日盘 09:00-10:00"]] if status == "SUCCESS" else []
    child = "FETCH_ERROR" if status == "FETCH_ERROR" else "OK"
    def collect(**values):
        return collect_akshare_contract(**values, _runner=lambda *_: ("CHILD_OK", {"status": child, "rows": rows}))
    return _original_observe(**kwargs, _collector=collect)
obs.observe = fake_observe
"""
)


def command(env, *args):
    process = subprocess.run(
        [sys.executable, "-m", "bt_api_py.trading_calendar_observation_run", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return (
        process.returncode,
        [json.loads(line) for line in process.stdout.splitlines()],
        process.stderr,
    )


def run_args(*extra):
    return (
        "run",
        "--mock",
        "--mock-year",
        "2026",
        "--budget-seconds",
        "20",
        "--trusted-local-posix",
        *extra,
    )


def explicit():
    return (
        "--akshare-venue",
        "SHFE",
        "--akshare-contract",
        "RB2701",
        "--akshare-date",
        "2026-11-13",
        "--akshare-timeout",
        "1",
    )


def test_default_never_attempts_akshare(tmp_path):
    env = env_without_network(tmp_path, OFFLINE)
    rc, lines, stderr = command(env, *run_args())
    run = lines[-1]
    try:
        assert rc == 10, (lines, stderr)
        assert "akshare_observation" not in run
        assert all(item.get("event") != "MOCK_AKSHARE_ID_ALLOCATED" for item in lines)
    finally:
        if os.path.isdir(run["simulation_root"]):
            shutil.rmtree(run["simulation_root"])


def test_explicit_akshare_keeps_day_source_and_only_fills_missing_night(tmp_path):
    env = env_without_network(tmp_path, OFFLINE)
    env["AKSHARE_FAKE"] = "SUCCESS"
    rc, lines, stderr = command(env, *run_args(*explicit()))
    result = lines[-1]
    try:
        assert rc == 10, (lines, stderr)
        allocated = next(item for item in lines if item.get("event") == "MOCK_AKSHARE_ID_ALLOCATED")
        source = result["akshare_observation"]
        assert source["observation_id"] == allocated["observation_id"]
        assert (
            source["observation_status"] == "SUCCESS" and source["observation_state"] == "COMMITTED"
        )
        semantic = source["candidate"]["semantic"]
        assert {(w["scope"], w["source_type"]) for w in semantic["windows"]} == {
            ("DAY", "AKSHARE_SYNTAX_CANDIDATE"),
            ("NIGHT", "MOCK"),
        }
        assert semantic["publication"] == semantic["account_use"] == "NOT_AUTHORIZED"
        assert semantic["business_snapshot"] == "NOT_A_BUSINESS_SNAPSHOT"
        assert not os.path.exists(os.path.join(result["simulation_root"], "CURRENT.json"))
        rc, shown, stderr = command(
            env,
            "mock-show",
            "--calendar-root",
            result["simulation_root"],
            "--run-id",
            result["run_id"],
            "--akshare-observation-id",
            source["observation_id"],
            "--trusted-local-posix",
        )
        assert rc == 10, (shown, stderr)
        candidate = shown[-1]["akshare_candidate"]
        assert candidate["original_write_outcome"] == "NOT_PROVEN"
        assert candidate["candidate"]["trace"]["observation_id"] == source["observation_id"]
        assert candidate["candidate"]["semantic"] == semantic
    finally:
        if os.path.isdir(result["simulation_root"]):
            shutil.rmtree(result["simulation_root"])


def test_empty_and_failed_source_preserve_status_and_use_fictional_windows(tmp_path):
    env = env_without_network(tmp_path, OFFLINE)
    for status in ("EMPTY", "FETCH_ERROR"):
        env["AKSHARE_FAKE"] = status
        rc, lines, stderr = command(env, *run_args(*explicit()))
        result = lines[-1]
        try:
            assert rc == 10, (lines, stderr)
            source = result["akshare_observation"]
            assert source["observation_status"] == status
            semantic = source["candidate"]["semantic"]
            assert semantic["source_status"] == status
            assert {w["scope"] for w in semantic["windows"]} == {"DAY", "NIGHT"}
            assert all(w["source_type"] == "MOCK" for w in semantic["windows"])
        finally:
            if os.path.isdir(result["simulation_root"]):
                shutil.rmtree(result["simulation_root"])


def test_uncertain_source_never_promotes_readback_or_publishes(tmp_path):
    env = env_without_network(tmp_path, OFFLINE)
    env["AKSHARE_FAKE"] = "ATTEMPT_UNKNOWN"
    rc, lines, stderr = command(env, *run_args(*explicit()))
    result = lines[-1]
    try:
        assert rc == 25, (lines, stderr)
        source = result["akshare_observation"]
        assert source["observation_state"] == "ATTEMPT_UNKNOWN" and "candidate" not in source
        assert source["observation_id"] == next(
            item["observation_id"]
            for item in lines
            if item.get("event") == "MOCK_AKSHARE_ID_ALLOCATED"
        )
        assert not os.path.exists(os.path.join(result["simulation_root"], "CURRENT.json"))
    finally:
        if os.path.isdir(result["simulation_root"]):
            shutil.rmtree(result["simulation_root"])


def test_mixed_candidate_rejects_publication_or_incomplete_scope(tmp_path):
    env = env_without_network(tmp_path, OFFLINE)
    for extra in ((*explicit(), "--mock-review-mode", "auto"), ("--akshare-contract", "RB2701")):
        rc, lines, stderr = command(env, *run_args(*extra))
        assert rc == 2 and lines[-1]["status"] == "ERROR", (lines, stderr)
        assert not any(item.get("event") == "MOCK_AKSHARE_ID_ALLOCATED" for item in lines)
