"""Offline mock capture and derived review; no account or network evidence."""

import json
import shutil
import subprocess
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py import trading_calendar_mock as mock
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_review_bundle as review
from tests.unit.test_trading_calendar_observation_run import env_without_network

FS = "TRUSTED_LOCAL_POSIX"


def args():
    return {
        "root": None,
        "config": None,
        "supplement": None,
        "budget_seconds": 30,
        "enable_shfe": False,
        "filesystem": FS,
        "run_id": uuid.uuid4().hex,
        "capture_id": "2" * 32,
        "parent_id": "3" * 32,
        "derived_id": "4" * 32,
        "mock_year": 2026,
    }


def test_deterministic_six_venue_two_year_fictional_schedules():
    data, supplement = mock.build(2026)
    assert (data, supplement) == mock.build(2026)
    assert data["metadata"] == {"data_origin": "MOCK", "environment": "SIMULATION_ONLY"}
    assert len(data["shards"]) == 6
    for shard in data["shards"]:
        snapshot = CalendarSnapshot(shard["layers"], calendar_ref="mock")
        venue = shard["venue"]
        for kind in ("future", "option"):

            def query(day, snapshot=snapshot, venue=venue, kind=kind):
                return snapshot.query(
                    venue=venue,
                    kind=kind,
                    product=f"mock_{kind}",
                    contract=f"mock_{venue.lower()}_{kind}_contract",
                    trading_day=day,
                )

            for year in (2026, 2027):
                assert query(date(year, 6, 2)).status == "COMPLETE"
                assert not query(date(year, 1, 15)).windows
                monday = date(year, 6, 1) + timedelta(days=(-date(year, 6, 1).weekday()) % 7)
                windows = query(monday).windows
                night = next(window for window in windows if window.window_id == "night")
                assert night.start_local.date() == monday - timedelta(days=3)
                assert any(window.window_id == "afternoon" for window in windows)
            assert query(date(2028, 1, 1)).status == "UNKNOWN"
            assert (
                not query(date(2026, 3, 2)).windows
                if kind == "future"
                else all(w.window_id != "day_pm" for w in query(date(2026, 3, 2)).windows)
            )
        future = snapshot.query(
            venue=venue,
            kind="future",
            product="mock_future",
            contract=f"mock_{venue.lower()}_future_contract",
            trading_day=date(2026, 6, 2),
        )
        night = next(window for window in future.windows if window.window_id == "night")
        assert night.start_local.date() == date(2026, 6, 1)
        assert night.end_local.date() == date(2026, 6, 2)
        after_holiday = snapshot.query(
            venue=venue,
            kind="future",
            product="mock_future",
            contract=f"mock_{venue.lower()}_future_contract",
            trading_day=date(2026, 1, 16),
        )
        assert all(window.window_id != "night" for window in after_holiday.windows)


def test_normal_run_auto_captures_and_rereads_derived(tmp_path):
    events = []
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_observation_run",
            "run",
            "--mock",
            "--mock-year",
            "2026",
            "--budget-seconds",
            "30",
            "--trusted-local-posix",
        ],
        env=env_without_network(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert child.returncode == 10, (child.stdout, child.stderr)
    lines = [json.loads(line) for line in child.stdout.splitlines()]
    events, result = lines[:-1], lines[-1]
    options = {
        **args(),
        **{key: result[key] for key in ("run_id", "capture_id", "parent_id", "derived_id")},
    }
    root = result.get("simulation_root")
    try:
        assert result["state"] == "INCOMPLETE" and result["candidate_state"] == "COMMITTED", repr(
            result
        )
        assert result["simulation_publication"] == "NOT_ATTEMPTED"
        assert [e["event"] for e in events] == ["MOCK_IDS_ALLOCATED", "MOCK_ROOT_CREATED"]
        assert result["data_origin"] == "MOCK" and result["environment"] == "SIMULATION_ONLY"
        assert not (Path(root) / "CURRENT.json").exists()
        parent = review.show(root=root, candidate_id=result["parent_id"], filesystem=FS)
        derived = review.show(root=root, candidate_id=result["derived_id"], filesystem=FS)
        assert parent["bundle"]["schema"].endswith("/2")
        assert derived["bundle"]["schema"].endswith("/4")
        assert derived["bundle"]["report"]["status"] == "STRUCTURALLY_READY_UNVERIFIED"
        assert derived["bundle"]["input"]["metadata"]["environment"] == "SIMULATION_ONLY"
        assert (
            runs.show_once(root=root, run_id=options["run_id"], filesystem=FS)[
                "derived_association"
            ]["state"]
            == "VISIBLE_UNAUTHENTICATED"
        )
        assert review.compare_full(root=root, candidate_id=result["derived_id"], filesystem=FS)[
            "status"
        ] not in {"SAME", "CHANGED"}
        assert (
            runs.run_once(**options, emit=lambda _: None)["reason"]
            == "RUN_ID_EXISTS_RECONCILE_FIRST"
        )
        shown = runs.show_once(root=root, run_id=options["run_id"], filesystem=FS)
        assert shown["mock_audit"]["state"] == "VISIBLE_UNAUTHENTICATED"
        assert shown["mock_audit"]["data_origin"] == "MOCK"
    finally:
        if root and any(event["event"] == "MOCK_ROOT_CREATED" for event in events):
            shutil.rmtree(root)


def test_existing_root_and_preallocation_failure_have_no_side_effect(tmp_path):
    events = []
    result = runs.run_once(**{**args(), "root": str(tmp_path)}, emit=events.append)
    assert result["reason"] == "INVALID_ARGUMENTS" and not events and not list(tmp_path.iterdir())
    result = runs.run_once(**args(), emit=lambda _: (_ for _ in ()).throw(RuntimeError()))
    assert result["reason"] == "ALLOCATION_NOT_CONFIRMED"
    assert not Path(result["simulation_root"]).exists()


def test_mock_cli_rejects_real_root_and_network_flags(tmp_path):
    base = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_observation_run",
        "run",
        "--mock",
        "--mock-year",
        "2026",
        "--budget-seconds",
        "30",
        "--trusted-local-posix",
    ]
    for extra in (
        ["--calendar-root", str(tmp_path)],
        ["--config", ""],
        ["--enable-shfe"],
        ["--enable-ine"],
    ):
        child = subprocess.run(
            base + extra, capture_output=True, text=True, timeout=20, check=False
        )
        assert child.returncode == 2
        assert "MOCK_IDS_ALLOCATED" not in child.stdout
    assert not list(tmp_path.iterdir())


def test_mock_cross_process_exclusive_run_id(tmp_path):
    options = args()
    second = {**options, "capture_id": "5" * 32, "parent_id": "6" * 32, "derived_id": "7" * 32}
    script = """import json,sys
from bt_api_py.trading_calendar_observation_run import run_once
events=[]
r=run_once(**json.loads(sys.argv[1]),emit=events.append)
print(json.dumps({'result':r,'events':events}))
"""
    env = env_without_network(tmp_path)
    commands = [[sys.executable, "-c", script, json.dumps(value)] for value in (options, second)]
    processes = [
        subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        for cmd in commands
    ]
    results = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=120)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
        winner = next(
            item for item in results if item["result"].get("candidate_state") == "COMMITTED"
        )
        loser = next(
            item for item in results if item["result"]["reason"] == "RUN_ID_EXISTS_RECONCILE_FIRST"
        )
        root = winner["result"]["simulation_root"]
        assert loser["result"]["simulation_root"] == root
        shown = runs.show_once(root=root, run_id=options["run_id"], filesystem=FS)
        assert shown["mock_audit"]["parent_id"] == winner["result"]["parent_id"]
        assert shown["mock_audit"]["derived_id"] == winner["result"]["derived_id"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
        for item in results:
            if any(e["event"] == "MOCK_ROOT_CREATED" for e in item["events"]):
                shutil.rmtree(item["result"]["simulation_root"])


def test_mock_write_unknown_only_inspects_original_id(tmp_path):
    options = args()
    script = """import json,sys
from bt_api_py import trading_calendar_observation_run as runs
original=runs._write_once
def uncertain(directory,chain,name,data):
    state,reason=original(directory,chain,name,data)
    if name.startswith('mock-') and state == 'COMMITTED':
        return 'COMMIT_UNKNOWN','INJECTED_AFTER_WRITE'
    return state,reason
runs._write_once=uncertain
events=[]
r=runs.run_once(**json.loads(sys.argv[1]),emit=events.append)
print(json.dumps({'result':r,'events':events}))
"""
    child = subprocess.run(
        [sys.executable, "-c", script, json.dumps(options)],
        env=env_without_network(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    payload = json.loads(child.stdout)
    result = payload["result"]
    root = result["simulation_root"]
    try:
        assert result["state"] == "COMMIT_UNKNOWN"
        assert result["reason"] == "INJECTED_AFTER_WRITE"
        shown = runs.show_once(root=root, run_id=options["run_id"], filesystem=FS)
        assert shown["mock_audit"]["state"] == "VISIBLE_UNAUTHENTICATED"
        assert shown["intent"] == "NOT_OBSERVED"
        assert (
            runs.run_once(**options, emit=lambda _: None)["reason"]
            == "RUN_ID_EXISTS_RECONCILE_FIRST"
        )
    finally:
        if any(e["event"] == "MOCK_ROOT_CREATED" for e in payload["events"]):
            shutil.rmtree(root)
