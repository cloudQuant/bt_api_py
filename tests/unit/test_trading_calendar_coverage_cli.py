"""One-shot coverage reports over synthetic local store declarations."""

import json
from datetime import UTC, date, datetime

import pytest

from bt_api_py import trading_calendar_coverage_cli as cli
from bt_api_py.trading_calendar_coverage_status import VENUES
from bt_api_py.trading_calendar_store import CalendarReadBudget
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_coverage_status import bundle, publish

NOW = datetime(2025, 12, 31, 16, tzinfo=UTC)


def report(root, *, instant=NOW, **kwargs):
    return cli.report_coverage(
        str(root),
        mode=kwargs.pop("mode", "SIMULATION_ONLY"),
        clock_error_seconds=kwargs.pop("clock_error_seconds", 0),
        _clock=lambda: instant,
        **kwargs,
    )


def files(root):
    return {p.relative_to(root): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


def test_real_store_reports_each_venue_and_does_not_mutate(tmp_path):
    starts = {venue: date(2025, 12, 1) for venue in VENUES}
    starts["GFEX"] = date(2026, 1, 2)
    ends = dict(
        zip(
            VENUES,
            (
                date(2026, 2, 1),
                date(2026, 1, 31),
                date(2026, 1, 1),
                date(2025, 12, 31),
                date(2026, 12, 31),
                date(2026, 12, 31),
            ),
            strict=True,
        )
    )
    store = FixtureCalendarStore(tmp_path)
    publish(store, bundle(ends, starts=starts), 1)
    before = files(tmp_path)
    result = report(tmp_path)
    assert files(tmp_path) == before
    assert result["exit_code"] == 2
    rows = {row["venue"]: row for row in result["venues"]}
    assert [
        (rows[v]["simulation_status"], rows[v]["simulation_days_remaining"]) for v in VENUES
    ] == [
        ("OK", 31),
        ("WARNING", 30),
        ("WARNING", 0),
        ("WARNING", -1),
        ("OK", 364),
        ("OUTSIDE_COVERAGE", None),
    ]
    for row in rows.values():
        assert row["version"] == "v1"
        assert row["coverage_start"] and row["coverage_end"]
        assert row["shanghai_date"] == "2026-01-01"
        assert row["real_health"] == "UNKNOWN"
        assert row["source_authentication"] == row["completeness"] == "NOT_PROVEN"
    assert result["scope"] == "SIMULATION_COVERAGE_DECLARATIONS_ONLY"
    assert result["clock_authentication"] == "NOT_PROVEN"
    assert result["trading_authorization"] == "NONE"


@pytest.mark.parametrize(
    "end,code,status",
    [
        (date(2026, 2, 1), 0, "OK"),
        (date(2026, 1, 31), 1, "WARNING"),
        (date(2025, 12, 31), 1, "WARNING"),
    ],
)
def test_exit_codes_and_custom_threshold(tmp_path, end, code, status):
    starts = {venue: date(2025, 1, 1) for venue in VENUES}
    publish(FixtureCalendarStore(tmp_path), bundle(end, starts=starts), 1)
    result = report(tmp_path)
    assert result["exit_code"] == code
    assert {row["simulation_status"] for row in result["venues"]} == {status}
    if end == date(2026, 2, 1):
        assert report(tmp_path, threshold_days=31)["exit_code"] == 1


def test_midnight_bound_and_single_clock_call(tmp_path):
    publish(FixtureCalendarStore(tmp_path), bundle(date(2026, 12, 31)), 1)
    instant = datetime(2026, 11, 30, 15, 59, 59, 900000, tzinfo=UTC)
    calls = []

    def clock():
        calls.append(instant)
        return instant

    result = cli.report_coverage(
        str(tmp_path), mode="SIMULATION_ONLY", clock_error_seconds=0.2, _clock=clock
    )
    assert len(calls) == 1 and result["exit_code"] == 2
    assert all(
        row["simulation_status"] == "UNKNOWN" and row["shanghai_date"] is None
        for row in result["venues"]
    )
    safe = report(tmp_path, instant=instant, clock_error_seconds=0.05)
    assert safe["exit_code"] == 0
    assert {row["shanghai_date"] for row in safe["venues"]} == {"2026-11-30"}


def test_missing_venue_current_and_corruption_are_unknown_without_writes(tmp_path):
    missing = tmp_path / "missing"
    assert report(missing)["exit_code"] == 2
    assert not missing.exists()
    root = tmp_path / "store"
    root.mkdir()
    publish(FixtureCalendarStore(root), bundle(date(2026, 12, 31), ("SHFE",)), 1)
    partial = report(root)
    assert partial["exit_code"] == 2
    assert (
        next(row for row in partial["venues"] if row["venue"] == "DCE")["declaration"]
        == "UNAVAILABLE"
    )
    (root / "CURRENT.json").write_bytes(b"invalid")
    before = files(root)
    corrupt = report(root)
    assert files(root) == before
    assert corrupt["exit_code"] == 2
    assert len(corrupt["venues"]) == 6
    assert all(row["simulation_status"] == "UNKNOWN" for row in corrupt["venues"])
    assert str(root) not in json.dumps(corrupt)


@pytest.mark.parametrize(
    "changes",
    [
        {"root": "relative"},
        {"root": "/tmp/../bad"},
        {"mode": "LIVE"},
        {"clock_error_seconds": -1},
        {"clock_error_seconds": float("inf")},
        {"clock_error_seconds": float("nan")},
        {"clock_error_seconds": True},
        {"clock_error_seconds": 86401},
        {"threshold_days": -1},
        {"threshold_days": True},
    ],
)
def test_strict_arguments_return_sanitized_unknown(tmp_path, changes):
    values = {
        "root": str(tmp_path / "private-path"),
        "mode": "SIMULATION_ONLY",
        "clock_error_seconds": 0,
        "_clock": lambda: NOW,
    }
    values.update(changes)
    result = cli.report_coverage(**values)
    assert result["exit_code"] == 2
    assert "private-path" not in json.dumps(result)


def test_cli_json_error_is_sanitized_and_reports_exit_code(tmp_path, capsys):
    path = str(tmp_path / "secret-path")
    code = cli.main(["--root", path, "--mode", "SIMULATION_ONLY", "--clock-error-seconds", "bad"])
    captured = capsys.readouterr()
    assert code == json.loads(captured.out)["exit_code"] == 2
    assert path not in captured.out + captured.err
    assert not (tmp_path / "secret-path").exists()


@pytest.mark.parametrize("end,code", [(date(2026, 2, 1), 0), (date(2026, 1, 31), 1), (None, 2)])
def test_cli_serializes_real_read_and_exits(tmp_path, capsys, monkeypatch, end, code):
    root = tmp_path / "private-root"
    if end is not None:
        root.mkdir()
        publish(FixtureCalendarStore(root), bundle(end), 1)
    real_report = cli.report_coverage

    def deterministic(*args, **kwargs):
        return real_report(*args, **kwargs, _clock=lambda: NOW)

    monkeypatch.setattr(cli, "report_coverage", deterministic)
    exit_code = cli.main(
        ["--root", str(root), "--mode", "SIMULATION_ONLY", "--clock-error-seconds", "0"]
    )
    captured = capsys.readouterr()
    assert exit_code == code == json.loads(captured.out)["exit_code"]
    assert str(root) not in captured.out + captured.err
    if end is None:
        assert not root.exists()


def test_clock_exception_is_not_disclosed(tmp_path):
    def bad_clock():
        raise RuntimeError("secret-private-clock-error")

    result = cli.report_coverage(
        str(tmp_path), mode="SIMULATION_ONLY", clock_error_seconds=0, _clock=bad_clock
    )
    assert result["exit_code"] == 2
    assert "secret-private-clock-error" not in json.dumps(result)


def test_oversized_current_is_unknown_and_does_not_create_files(tmp_path):
    publish(FixtureCalendarStore(tmp_path), bundle(date(2026, 12, 31)), 1)
    assert report(tmp_path)["exit_code"] == 0
    with (tmp_path / "CURRENT.json").open("wb") as current:
        current.truncate(cli._READ_BUDGET.max_file_bytes + 1)
    before = files(tmp_path)
    result = report(tmp_path)
    assert result["exit_code"] == 2
    assert all(row["simulation_status"] == "UNKNOWN" for row in result["venues"])
    assert result["read_limits"] == {
        "max_file_bytes": 8 * 1024 * 1024,
        "max_total_bytes": 64 * 1024 * 1024,
        "max_publications": 2048,
        "wall_clock_timeout": "NOT_GUARANTEED",
        "limit_exceeded": "UNKNOWN",
    }
    assert files(tmp_path) == before
    assert str(tmp_path) not in json.dumps(result)


def test_publication_count_and_total_read_limits_fail_closed(tmp_path, monkeypatch):
    store = FixtureCalendarStore(tmp_path)
    data = bundle(date(2026, 12, 31))
    publish(store, data, 1)
    publish(store, bundle(date(2027, 12, 31)), 2)
    assert report(tmp_path)["exit_code"] == 0
    before = files(tmp_path)
    for budget in (
        CalendarReadBudget(8 * 1024 * 1024, 64 * 1024 * 1024, 1),
        CalendarReadBudget(8 * 1024 * 1024, 128, 2048),
    ):
        monkeypatch.setattr(cli, "_READ_BUDGET", budget)
        result = report(tmp_path)
        assert result["exit_code"] == 2
        assert all(row["simulation_status"] == "UNKNOWN" for row in result["venues"])
    assert files(tmp_path) == before


def test_missing_or_busy_lock_is_unknown_without_writes(tmp_path):
    fcntl = pytest.importorskip("fcntl")
    publish(FixtureCalendarStore(tmp_path), bundle(date(2026, 12, 31)), 1)
    lock = tmp_path / ".lock"
    before = files(tmp_path)
    with lock.open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = report(tmp_path)
            assert result["exit_code"] == 2
            assert all(row["simulation_status"] == "UNKNOWN" for row in result["venues"])
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    assert files(tmp_path) == before
    lock.unlink()
    before = files(tmp_path)
    result = report(tmp_path)
    assert result["exit_code"] == 2 and not lock.exists()
    assert files(tmp_path) == before
    assert str(tmp_path) not in json.dumps(result)
