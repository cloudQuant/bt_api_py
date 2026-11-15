"""Caller-declared MOCK staleness reports do not assert real calendar facts."""

import io
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from bt_api_py import trading_calendar_staleness_simulation as target


def day(value, day_status="OPEN", check_status="NO_SUCCESS", deadline_elapsed=True):
    return {
        "day": value,
        "day_status": day_status,
        "check_status": check_status,
        "deadline_elapsed": deadline_elapsed,
    }


def observations(days):
    return {venue: {"scope_id": "mock-scope", "days": deepcopy(days)} for venue in target.VENUES}


def report(value, *, clock=datetime(2026, 11, 9, 4, tzinfo=UTC), error=0, threshold=2):
    return target.report_staleness(
        value,
        mode="SIMULATION_ONLY",
        clock_instant=clock,
        clock_error_seconds=error,
        threshold=threshold,
    )


def test_two_trading_days_across_weekend_are_conditional_warnings():
    days = [
        day("2026-11-06"),
        day("2026-11-07", "CLOSED"),
        day("2026-11-08", "CLOSED"),
        day("2026-11-09"),
    ]
    result = report(observations(days))
    assert (result["status"], result["exit_code"]) == ("WARNING", 1)
    assert all(row["min_failed_trading_days"] == 2 for row in result["venues"])
    assert all(row["max_failed_trading_days"] is None for row in result["venues"])
    assert result["environment"] == "SIMULATION_ONLY"
    assert result["real_health"] == "UNKNOWN" and result["source_authentication"] == "NOT_PROVEN"


def test_success_even_on_holiday_resets_and_scopes_are_independent():
    days = [
        day("2026-11-06"),
        day("2026-11-07", "CLOSED", "SUCCESS", False),
        day("2026-11-08", "CLOSED"),
        day("2026-11-09"),
    ]
    value = observations(days)
    value["DCE"]["days"][1]["check_status"] = "NO_SUCCESS"
    value["DCE"]["days"][1]["deadline_elapsed"] = True
    result = report(value)
    states = {row["venue"]: row["simulation_status"] for row in result["venues"]}
    assert states == {venue: "WARNING" if venue == "DCE" else "OK" for venue in target.VENUES}
    assert result["exit_code"] == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data["SHFE"]["days"][-1].update(day_status="UNKNOWN"),
        lambda data: data["SHFE"]["days"][-1].update(check_status="UNKNOWN"),
        lambda data: data["SHFE"]["days"][-1].update(deadline_elapsed=None),
        lambda data: data["SHFE"]["days"][-1].update(deadline_elapsed=False),
        lambda data: data["SHFE"]["days"][-1].pop("deadline_elapsed"),
        lambda data: data["SHFE"]["days"].pop(1),
        lambda data: data.pop("SHFE"),
    ],
)
def test_missing_or_uncertain_claims_are_not_promoted_to_warning(change):
    value = observations([day("2026-11-07"), day("2026-11-08"), day("2026-11-09")])
    change(value)
    result = report(value)
    assert result["venues"][0]["simulation_status"] == "UNKNOWN"
    assert result["exit_code"] == 2
    assert all(row["simulation_status"] == "WARNING" for row in result["venues"][1:])


def test_clock_midnight_ambiguity_and_invalid_inputs_are_unknown():
    value = observations([day("2026-11-08"), day("2026-11-09")])
    assert report(value, clock=datetime(2026, 11, 8, 16, tzinfo=UTC), error=1)["exit_code"] == 2
    assert report(value, error=float("nan"))["exit_code"] == 2
    assert report(value, threshold=True)["exit_code"] == 2
    assert (
        target.report_staleness(
            value, mode="LIVE", clock_instant=datetime.now(UTC), clock_error_seconds=0
        )["exit_code"]
        == 2
    )


def test_threshold_cross_year_and_as_of_gap():
    value = observations(
        [
            day("2026-12-30", check_status="SUCCESS"),
            day("2026-12-31"),
            day("2027-01-01", "CLOSED"),
            day("2027-01-02"),
        ]
    )
    result = report(value, clock=datetime(2027, 1, 2, 4, tzinfo=UTC), threshold=3)
    assert result["exit_code"] == 0
    assert all(row["min_failed_trading_days"] == 2 for row in result["venues"])
    assert report(value, clock=datetime(2027, 1, 3, 4, tzinfo=UTC))["exit_code"] == 2


def test_malformed_dates_and_unbounded_history_are_unknown():
    value = observations([day("2026-11-08"), day("2026-11-09")])
    value["DCE"]["days"] = [day("2026-11-09"), day("2026-11-09")]
    value["GFEX"]["days"] = [day("2026-11-09")] * (target.MAX_DAYS + 1)
    result = report(value)
    states = {row["venue"]: row["simulation_status"] for row in result["venues"]}
    assert states["DCE"] == states["GFEX"] == "UNKNOWN"
    assert states["SHFE"] == "WARNING"


def test_cli_uses_stdin_once_without_leaking_bad_input(monkeypatch, capsys):
    class Stdin:
        buffer = io.BytesIO(b'{"secret":"private-value","secret":1}')

    monkeypatch.setattr(target.sys, "stdin", Stdin())
    assert target.main(["--mode", "SIMULATION_ONLY", "--clock-error-seconds", "1"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "UNKNOWN"
    assert "private-value" not in captured.out + captured.err
    assert not captured.err


def test_cli_emits_simulated_warning(monkeypatch, capsys):
    today, error = target._local_date(datetime.now(UTC), timedelta(0))
    assert error is None
    days = [day((today - timedelta(days=1)).isoformat()), day(today.isoformat())]

    class Stdin:
        buffer = io.BytesIO(json.dumps(observations(days)).encode())

    monkeypatch.setattr(target.sys, "stdin", Stdin())
    assert target.main(["--mode", "SIMULATION_ONLY", "--clock-error-seconds", "0"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "WARNING" and result["real_health"] == "UNKNOWN"
