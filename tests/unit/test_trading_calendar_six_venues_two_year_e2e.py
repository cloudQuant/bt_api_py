"""Full fictional two-year CLI-to-bound-session simulation, never venue evidence.

The CTP snapshot must expose query_venue_day. The locally verified temporary
patch has not been applied to the SDK's CTP submodule. This is not a test of
manual review, fresh AKShare observations, installed packages, or accounts.
"""

import json
import os
import socket
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import bt_api_ctp.calendar_snapshot as ctp_snapshot
import pytest

from bt_api_py.trading_calendar_binding import GateDecision, SimulationCalendarBinding
from bt_api_py.trading_calendar_bound_session_simulation import evaluate_bound_session
from bt_api_py.trading_calendar_mock import SOURCE, VENUES, build
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.unit.test_trading_calendar_observation_run import env_without_network

UTC = timezone.utc  # noqa: UP017 - Python 3.9


class SimulationGate:
    def verify(self, event, context):
        assert context.route == "mock-route"
        assert context.source_generation == "mock-generation"
        return event


def cli(env, *args):
    result = subprocess.run(
        [sys.executable, "-m", "bt_api_py.trading_calendar_observation_run", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return (
        result.returncode,
        [json.loads(line) for line in result.stdout.splitlines()],
        result.stderr,
    )


def scope(venue, kind):
    return {
        "venue": venue,
        "kind": kind,
        "product": f"mock_{kind}",
        "contract": f"mock_{venue.lower()}_{kind}_contract",
    }


def bind(store, day):
    return SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=store,
        snapshot_factory=ctp_snapshot.CalendarSnapshot,
        gate=SimulationGate(),
        route="mock-route",
        source_generation="mock-generation",
        trading_day=day,
        last_sequence=1,
    )


def evaluate(bound, values, owner, instant):
    return evaluate_bound_session(bound, **values, owner_day=owner, at_utc=instant)


def test_full_mock_two_year_publication_sessions_and_gated_switch(tmp_path, monkeypatch):
    if not callable(getattr(ctp_snapshot.CalendarSnapshot, "query_venue_day", None)):
        pytest.skip("CTP CalendarSnapshot lacks required query_venue_day interface")

    def no_connect(*args, **kwargs):
        raise AssertionError("network is forbidden in this offline test")

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    root = Path(os.path.realpath(tmp_path)) / "calendar"
    root.mkdir(mode=0o700)
    config = root.parent / "config.json"
    config.write_text(json.dumps({"calendars": {"root": str(root), "review_mode": "auto"}}))
    env = env_without_network(tmp_path)
    rc, lines, err = cli(
        env,
        "run",
        "--mock",
        "--mock-year",
        "2026",
        "--calendar-root",
        str(root),
        "--config",
        str(config),
        "--budget-seconds",
        "300",
        "--trusted-local-posix",
    )
    assert rc == 0, (lines, err)
    capture = next(row for row in lines if row.get("candidate_state") == "COMMITTED")
    assert capture["simulation_root"] == str(root)
    run_id = capture["run_id"]
    first = lines[-1]
    assert (first["status"], first["current_visibility"], first["publication"]) == (
        "COMMITTED",
        "VISIBLE",
        "SIMULATION_ONLY",
    )
    assert first["result"]["fence"] == "CLEAR" and first["result"]["post_commit"] == "COMPLETE"
    assert first["data_origin"] == "MOCK" and first["source_authentication"] == "NOT_PROVEN"
    store = TradingCalendarStore(str(root))
    current = store.read_current()
    assert (current.publication_id, current.version_id) == (
        first["publication_id"],
        first["version_id"],
    )
    assert store.verify_publication(first["publication_id"]) == "VISIBLE"
    bound = bind(store, date(2026, 6, 8))
    assert bound.status().calendar_ref == first["version_id"]

    for venue in VENUES:
        for day in (date(2026, 6, 8), date(2027, 1, 4)):
            venue_day = bound.query_venue_day(mode="SIMULATION_ONLY", venue=venue, trading_day=day)
            assert (venue_day.status, venue_day.day_status, venue_day.source_ids) == (
                "COMPLETE",
                "open",
                (SOURCE["id"],),
            )
            assert (venue_day.source_authentication, venue_day.trade_authorization) == (
                "NOT_PROVEN",
                "NONE",
            )
            for kind in ("future", "option"):
                values = scope(venue, kind)
                result = bound.query(**values, trading_day=day)
                assert result.result.status == "COMPLETE"
                assert result.result.source_ids == (SOURCE["id"],)
                windows = {window.window_id: window for window in result.result.windows}
                assert set(windows) == {"night", "day_am", "day_pm", "afternoon"}
                assert windows["day_am"].start_local.strftime("%H:%M") == "09:00"
                assert windows["day_pm"].end_local.strftime("%H:%M") == (
                    "11:30" if kind == "future" else "11:00"
                )
                assert windows["afternoon"].end_local.strftime("%H:%M") == (
                    "15:00" if kind == "future" else "14:45"
                )
                previous_friday = date(2026, 6, 5) if day.year == 2026 else date(2027, 1, 1)
                assert windows["night"].start_local.date() == previous_friday
                assert windows["night"].start_local.strftime("%H:%M") == "21:00"
                assert windows["night"].end_local.strftime("%H:%M") == (
                    "01:00" if kind == "future" else "23:00"
                )
                assert (windows["night"].end_local.date() > previous_friday) == (kind == "future")
                after_midnight = datetime.combine(
                    previous_friday, datetime.min.time(), UTC
                ).replace(hour=16, minute=30)
                midnight_result = evaluate(bound, values, day, after_midnight)
                assert midnight_result.phase == ("CONTINUOUS" if kind == "future" else "UNKNOWN")
                assert evaluate(bound, values, previous_friday, after_midnight).phase == "UNKNOWN"
                for name, window in windows.items():
                    instant = window.start_utc + (window.end_utc - window.start_utc) / 2
                    evaluation = evaluate(bound, values, day, instant)
                    assert evaluation.phase == "CONTINUOUS" and evaluation.owner_day == day
                    assert evaluation.window.window_id == name
                    assert evaluation.window_provenance == {
                        "scope": "NIGHT" if name == "night" else "DAY",
                        "window_id": name,
                        "source_type": "MOCK",
                        "source_id": SOURCE["id"],
                        "source_authentication": "NOT_PROVEN",
                        "declaration": "PINNED_RULE_AND_OWNER_DAY",
                    }
                    assert evaluation.trade_authorization == "NONE"
                assert (
                    evaluate(
                        bound, values, day, datetime(day.year, day.month, day.day, 4, tzinfo=UTC)
                    ).phase
                    == "UNKNOWN"
                )
                assert (
                    evaluate(
                        bound, values, date(2028, 1, 3), datetime(2028, 1, 3, 2, tzinfo=UTC)
                    ).phase
                    == "UNKNOWN"
                )
        for closed in (date(2026, 1, 15), date(2027, 1, 15)):
            result = bound.query_venue_day(mode="SIMULATION_ONLY", venue=venue, trading_day=closed)
            assert (result.status, result.day_status, result.source_ids) == (
                "COMPLETE",
                "closed",
                (SOURCE["id"],),
            )
        assert (
            bound.query_venue_day(
                mode="SIMULATION_ONLY", venue=venue, trading_day=date(2028, 1, 1)
            ).status
            == "UNKNOWN"
        )
        exception = date(2026, 3, 2)
        future = bound.query(**scope(venue, "future"), trading_day=exception).result
        option = bound.query(**scope(venue, "option"), trading_day=exception).result
        assert future.status == option.status == "COMPLETE"
        assert future.windows == ()
        assert "day_pm" not in {window.window_id for window in option.windows}
        assert {window.window_id for window in option.windows} == {"night", "day_am", "afternoon"}

    # The initial CLI uses its synthetic supplement, while a configured-root
    # update requires an explicit maintained supplement on disk.
    (root / "supplement.yaml").write_text(json.dumps(build(2026)[1]))
    rc, lines, err = cli(
        env,
        "mock-update",
        "--calendar-root",
        str(root),
        "--run-id",
        run_id,
        "--variant",
        "AFTERNOON_CHANGE",
        "--trusted-local-posix",
    )
    assert rc == 0, (lines, err)
    second = lines[-1]
    assert (second["status"], second["current_visibility"], second["publication"]) == (
        "COMMITTED",
        "VISIBLE",
        "SIMULATION_ONLY",
    )
    assert second["result"]["fence"] == "CLEAR" and second["result"]["post_commit"] == "COMPLETE"
    assert store.verify_publication(second["publication_id"]) == "VISIBLE"
    assert second["version_id"] != first["version_id"]
    sample = scope("SHFE", "future")
    old_day = date(2026, 6, 8)
    instant = datetime(2026, 6, 8, 6, 45, tzinfo=UTC)
    assert evaluate(bound, sample, old_day, instant).phase == "CONTINUOUS"
    assert bound.status().calendar_ref == first["version_id"]
    restarted = bind(store, old_day)
    assert restarted.status().calendar_ref == second["version_id"]
    assert restarted.status().binding_reason == "PROCESS_START_OR_RESTART"
    assert evaluate(restarted, sample, old_day, instant).phase == "UNKNOWN"
    restart_script = """
import json, sys
from datetime import date, datetime, timezone
from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_py.trading_calendar_binding import SimulationCalendarBinding
from bt_api_py.trading_calendar_bound_session_simulation import evaluate_bound_session
from bt_api_py.trading_calendar_store import TradingCalendarStore
class Gate:
    def verify(self, event, context):
        return event
bound = SimulationCalendarBinding(mode='SIMULATION_ONLY', store=TradingCalendarStore(sys.argv[1]),
    snapshot_factory=CalendarSnapshot, gate=Gate(), route='mock-route',
    source_generation='mock-generation', trading_day=date(2026, 6, 8), last_sequence=1)
status = bound.status()
query = evaluate_bound_session(bound, venue='SHFE', kind='future', product='mock_future',
    contract='mock_shfe_future_contract', owner_day=date(2026, 6, 8),
    at_utc=datetime(2026, 6, 8, 6, 45, tzinfo=timezone.utc))
print(json.dumps({'version': status.calendar_ref, 'reason': status.binding_reason, 'phase': query.phase}))
"""
    restarted_process = subprocess.run(
        [sys.executable, "-c", restart_script, str(root)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert restarted_process.returncode == 0, restarted_process.stderr
    assert json.loads(restarted_process.stdout) == {
        "version": second["version_id"],
        "reason": "PROCESS_START_OR_RESTART",
        "phase": "UNKNOWN",
    }
    rejected = bound.activate(
        GateDecision(False, "mock-route", "mock-generation", date(2026, 6, 9), 2, "rejected")
    )
    assert (
        rejected.outcome == "GATE_REJECTED" and bound.status().calendar_ref == first["version_id"]
    )
    switched = bound.activate(
        GateDecision(True, "mock-route", "mock-generation", date(2026, 6, 9), 2, "switch")
    )
    assert switched.outcome == "ACTIVATED" and switched.binding.calendar_ref == second["version_id"]
    assert switched.binding.binding_reason == "GATED_DAY_SWITCH"
    assert (
        evaluate(bound, sample, date(2026, 6, 9), datetime(2026, 6, 9, 6, 45, tzinfo=UTC)).phase
        == "UNKNOWN"
    )
    window = next(
        w
        for w in bound.query(**sample, trading_day=date(2026, 6, 9)).result.windows
        if w.window_id == "afternoon"
    )
    assert window.end_local.strftime("%H:%M") == "14:30"
