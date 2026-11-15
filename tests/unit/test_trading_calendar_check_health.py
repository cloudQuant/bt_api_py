"""Read-only consecutive-trading-day check-health detection (D67 calendar store)."""

import json
from datetime import date

from bt_api_py import trading_calendar_schedule_once as schedule
from tests.unit.test_trading_calendar_schedule_once import (
    _schedule_subprocess,
    _snapshot_tree,
    execute,
    setup_root,
)

# The store fixture declares SHFE open on 2027-01-01 and 2027-01-04, closed on
# 2026-12-31 and the 2027-01-02/03 weekend. These are store declarations, not
# wall-clock weekdays: 2027-01-01 is a real exchange holiday.
EARLIER = "2026-12-30T16:30:00+00:00"  # Asia/Shanghai 2026-12-31 (declared closed)
FIRST_OPEN = "2026-12-31T16:30:00+00:00"  # Asia/Shanghai 2027-01-01
SECOND_OPEN = "2027-01-03T16:30:00+00:00"  # Asia/Shanghai 2027-01-04


def health(root, **kwargs):
    return schedule.inspect_check_health(
        root=str(root), venue="SHFE", filesystem=schedule.FS, **kwargs
    )


def test_two_consecutive_trading_days_without_records_warns_and_skips_gaps(tmp_path):
    root, env, _, path = setup_root(tmp_path)
    assert execute(env, _schedule_subprocess(env, path, date_utc=EARLIER))["status"] == "COMMITTED"
    result = health(root, as_of_date=date(2027, 1, 4))
    assert result["status"] == "WARNING", result
    assert result["reason"] == "TWO_CONSECUTIVE_TRADING_DAYS_NO_SUCCESS"
    assert result["trading_days"] == ["2027-01-01", "2027-01-04"]
    assert [row["day"] for row in result["days"]] == ["2027-01-01", "2027-01-04"]
    assert all(row["status"] == "NO_SUCCESS" for row in result["days"])
    assert all("NO_CHECK_RECORD" in row["reason_codes"] for row in result["days"])
    for skipped in ("2026-12-31", "2027-01-02", "2027-01-03"):
        assert skipped not in result["trading_days"]


def test_successful_checks_on_two_trading_days_are_ok(tmp_path):
    root, env, _, path = setup_root(tmp_path)
    assert execute(env, _schedule_subprocess(env, path, date_utc=FIRST_OPEN))["status"] in {
        "COMMITTED",
        "SAME",
    }
    second = execute(env, _schedule_subprocess(env, path, date_utc=SECOND_OPEN))
    assert second["status"] in {"COMMITTED", "SAME"}, second
    result = health(root, as_of_date=date(2027, 1, 4))
    assert result["status"] == "OK", result
    assert result["reason"] == "WITHIN_THRESHOLD"
    assert result["trading_days"] == ["2027-01-01", "2027-01-04"]
    assert [row["status"] for row in result["days"]] == ["SUCCESS", "SUCCESS"]


def test_missing_store_layout_is_unknown_and_read_only_is_preserved(tmp_path):
    result = health(tmp_path / "absent-root", as_of_date=date(2027, 1, 4))
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "CHECK_HISTORY_UNAVAILABLE"

    root, env, _, path = setup_root(tmp_path)
    assert execute(env, _schedule_subprocess(env, path, date_utc=EARLIER))["status"] == "COMMITTED"
    before = _snapshot_tree(root)
    health(root, as_of_date=date(2027, 1, 4))
    assert _snapshot_tree(root) == before


def test_invalid_venue_or_as_of_date_is_rejected(tmp_path):
    root, _, _, _ = setup_root(tmp_path)
    assert (
        schedule.inspect_check_health(
            root=str(root), venue="XXXX", as_of_date=date(2027, 1, 4), filesystem=schedule.FS
        )["status"]
        == "UNKNOWN"
    )
    assert health(root, as_of_date="2027-01-04")["status"] == "UNKNOWN"


def test_check_health_cli_reports_warning(tmp_path, capsys):
    root, env, _, path = setup_root(tmp_path)
    assert execute(env, _schedule_subprocess(env, path, date_utc=EARLIER))["status"] == "COMMITTED"
    code = schedule.main(
        [
            "--action",
            "check-health",
            "--root",
            str(root),
            "--venue",
            "SHFE",
            "--as-of-date",
            "2027-01-04",
            "--trusted-local-posix",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 10
    assert output["status"] == "WARNING"
    assert output["clock"] == "LOCAL_CLOCK_UNVERIFIED"
