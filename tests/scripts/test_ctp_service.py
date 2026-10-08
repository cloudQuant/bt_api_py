"""Offline scheduling and shutdown contracts for the CTP collector service."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CTP_SRC = ROOT / "bt_api" / "bt_api_ctp" / "src"
sys.path.insert(0, str(CTP_SRC))

from bt_api_ctp.collector.schedule import TradingCalendar  # noqa: E402

from ctp_data import service  # noqa: E402


def test_next_group_open_uses_current_session_and_preopen() -> None:
    calendar = TradingCalendar()

    assert service.next_group_open(datetime(2026, 9, 21, 10, 0), calendar=calendar) == (
        datetime(2026, 9, 21, 9, 0),
        0,
    )
    assert service.next_group_open(datetime(2026, 9, 21, 8, 50), calendar=calendar) == (
        datetime(2026, 9, 21, 9, 0),
        0,
    )
    assert service.next_group_open(datetime(2026, 9, 21, 15, 20), calendar=calendar) == (
        datetime(2026, 9, 21, 21, 0),
        1,
    )


def test_next_group_open_skips_weekends_and_calendar_holidays() -> None:
    weekend = service.next_group_open(datetime(2026, 9, 26, 12, 0))
    assert weekend == (datetime(2026, 9, 28, 9, 0), 0)

    calendar = TradingCalendar(holidays=frozenset({"20260922"}))
    after_close = service.next_group_open(datetime(2026, 9, 21, 15, 20), calendar=calendar)
    assert after_close == (datetime(2026, 9, 23, 9, 0), 0)


def test_night_session_after_midnight_keeps_previous_open_date() -> None:
    target = service.next_group_open(datetime(2026, 9, 26, 1, 0))
    assert target == (datetime(2026, 9, 25, 21, 0), 1)

    command = service.collector_command(
        Path("collector.yaml"),
        night=True,
        stop_file=Path("service.stop"),
        session_day=target[0].date(),
    )
    assert command[command.index("--date") + 1] == "20260925"
    assert command[command.index("--stop-file") + 1] == "service.stop"

    same_day_command = service.collector_command(
        Path("collector.yaml"), night=True, session_day=date(2026, 9, 25)
    )
    assert same_day_command[same_day_command.index("--date") + 1] == "20260925"


def test_large_lead_stays_on_target_date_after_previous_group_close() -> None:
    day_target = datetime(2026, 9, 21, 9, 0)
    night_target = datetime(2026, 9, 21, 21, 0)

    assert service._effective_start_at(day_target, 0, 1_000_000) == datetime(2026, 9, 21, 2, 30)
    assert service._effective_start_at(night_target, 1, 1_000_000) == datetime(2026, 9, 21, 15, 15)
    assert service._effective_start_at(day_target, 0, 900) == datetime(2026, 9, 21, 8, 45)
    assert service._effective_start_at(night_target, 1, 900) == datetime(2026, 9, 21, 20, 45)


def test_after_advances_past_the_group_already_handled() -> None:
    target = datetime(2026, 9, 21, 9, 0)
    assert service.next_group_open(datetime(2026, 9, 21, 10, 0), after=target) == (
        datetime(2026, 9, 21, 21, 0),
        1,
    )
    assert service.next_group_open(datetime(2026, 9, 21, 22, 0), after=target) == (
        datetime(2026, 9, 21, 21, 0),
        1,
    )


def test_scheduler_loads_holidays_from_the_collector_config(tmp_path: Path) -> None:
    holidays = tmp_path / "holidays.json"
    holidays.write_text('["20260921"]', encoding="utf-8")
    config = tmp_path / "collector.yaml"
    config.write_text(
        "data_root: ./data\ncalendar:\n  holidays_file: holidays.json\n", encoding="utf-8"
    )

    _payload, calendar = service._load_scheduler_calendar(config, tmp_path)

    assert not calendar.is_trading_day("20260921")


def test_retry_is_limited_to_time_before_the_session_close() -> None:
    target = datetime(2026, 9, 21, 9, 0)
    assert service._retry_delay(target, 0, now=datetime(2026, 9, 21, 14, 0), backoff=300) == 300
    assert service._retry_delay(target, 0, now=datetime(2026, 9, 21, 15, 14), backoff=300) is None
    assert service._retry_delay(target, 0, now=datetime(2026, 9, 21, 15, 16), backoff=0) is None


def test_run_collection_waits_for_stop_file_without_signalling_child(
    tmp_path: Path, monkeypatch
) -> None:
    stop_file = tmp_path / "service.stop"
    waits: list[float] = []
    spawn_options = []

    class _Process:
        def wait(self, *, timeout: float) -> int:
            waits.append(timeout)
            if len(waits) == 1:
                stop_file.touch()
                raise subprocess.TimeoutExpired("collector", timeout)
            return 0

    def spawn(*_args, **kwargs):
        spawn_options.append(kwargs)
        return _Process()

    monkeypatch.setattr(service.subprocess, "Popen", spawn)
    service._stop.clear()
    service._stop_file = stop_file
    try:
        assert service.run_collection(["offline-collector"], repo_root=tmp_path) == 0
        assert spawn_options[0]["stdout"] is sys.stdout
        assert spawn_options[0]["stderr"] is sys.stderr
        if os.name == "nt":
            assert spawn_options[0]["creationflags"] & subprocess.CREATE_NO_WINDOW
    finally:
        service._stop_file = None
        service._stop.clear()

    assert waits == [0.2, 0.2]
    assert stop_file.exists()


def test_dry_run_finishes_after_one_schedule_and_hides_config_values(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config = tmp_path / "collector.yaml"
    config.write_text(
        'data_root: "./data"\nctp:\n  password: "DO_NOT_LOG_THIS"\n', encoding="utf-8"
    )
    ready_file = tmp_path / "service.ready"
    planned = (datetime(2026, 9, 21, 9, 0), 0)
    calls = 0

    def _next_open(_now, *, after=None, calendar=None):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("dry-run requested more than one schedule")
        return planned

    monkeypatch.setattr(service, "next_group_open", _next_open)
    assert (
        service.main(["--config", str(config), "--dry-run", "--ready-file", str(ready_file)])
        == service.EXIT_OK
    )

    assert calls == 1
    assert not ready_file.exists()
    output = capsys.readouterr()
    assert "DO_NOT_LOG_THIS" not in output.out + output.err


def test_ready_file_contains_controller_identity_and_is_removed_on_exit(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "collector.yaml"
    config.write_text("data_root: ./data\n", encoding="utf-8")
    ready_file = tmp_path / "service.ready"
    observed: dict[str, object] = {}

    def _run_loop(_args, *, config, repo_root, calendar, initial_schedule):
        observed.update(json.loads(ready_file.read_text(encoding="utf-8")))
        return service.EXIT_OK

    monkeypatch.setattr(service, "_run_loop", _run_loop)
    assert (
        service.main(["--config", str(config), "--ready-file", str(ready_file)]) == service.EXIT_OK
    )

    assert observed["pid"] == os.getpid()
    assert observed["config"] == str(config.resolve())
    assert isinstance(observed["started_at"], str)
    assert not ready_file.exists()


def test_service_process_stops_collector_through_shared_marker(tmp_path: Path) -> None:
    config = tmp_path / "collector.yaml"
    config.write_text("data_root: ./data\n", encoding="utf-8")
    stop_file = tmp_path / "service.stop"
    ready_file = tmp_path / "service.ready"
    child_ready_file = tmp_path / "collector.ready"
    child_code = """
import argparse
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--child-ready-file', required=True)
parser.add_argument('--stop-file', required=True)
args, _unknown = parser.parse_known_args()
Path(args.child_ready_file).write_text('ready', encoding='utf-8')
while not Path(args.stop_file).exists():
    time.sleep(0.05)
"""
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(CTP_SRC), str(ROOT), existing_pythonpath) if part
    )
    env["SERVICE_COLLECTOR_CMD"] = shlex.join(
        [
            sys.executable,
            "-c",
            child_code,
            "--child-ready-file",
            str(child_ready_file),
        ]
    )
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "ctp_data" / "service.py"),
            "--config",
            str(config),
            "--lead",
            "1000000",
            "--retry",
            "0",
            "--once",
            "--stop-file",
            str(stop_file),
            "--ready-file",
            str(ready_file),
            "--log-file",
            str(tmp_path / "service.log"),
        ],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    output = ""
    try:
        deadline = time.monotonic() + 10
        while not (ready_file.exists() and child_ready_file.exists()):
            if process.poll() is not None:
                output, _ = process.communicate()
                raise AssertionError(f"service exited before launching collector:\n{output}")
            if time.monotonic() >= deadline:
                raise AssertionError("service or offline collector did not become ready")
            time.sleep(0.02)

        ready = json.loads(ready_file.read_text(encoding="utf-8"))
        assert ready["pid"] == process.pid
        assert ready["config"] == str(config.resolve())
        stop_file.touch()
        output, _ = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            stop_file.touch()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()

    assert process.returncode == service.EXIT_OK, output
    assert not ready_file.exists()
    assert stop_file.exists()
