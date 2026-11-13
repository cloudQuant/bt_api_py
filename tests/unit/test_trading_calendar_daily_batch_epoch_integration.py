"""Offline mixed-batch to prepared-epoch integration, not venue evidence."""

import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_ctp.simulation_calendar_ports import SimulationMapping

from bt_api_py.trading_calendar_epoch_simulation import (
    SimulationCalendarEpochCoordinator,
    SimulationEpochClosure,
    SimulationEpochEvent,
)
from bt_api_py.trading_calendar_mock import SOURCE
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.unit.test_trading_calendar_daily_batch import FAKE_BATCH, scope
from tests.unit.test_trading_calendar_daily_batch_identity import bundle
from tests.unit.test_trading_calendar_epoch_simulation import Gate
from tests.unit.test_trading_calendar_mixed_simulation_version import template_fixture
from tests.unit.test_trading_calendar_mock_fresh_observation import confirm
from tests.unit.test_trading_calendar_mock_persistent import run_args, settings
from tests.unit.test_trading_calendar_observation_run import env_without_network

OWNER = date(2027, 1, 4)
MAPPINGS = tuple(
    SimulationMapping(
        f"sim.{venue.lower()}", venue, "future", "pfuture", f"mock_{venue.lower()}_future_contract"
    )
    for venue in ("SHFE", "DCE")
)


def annual_fixture():
    """Extend only two fictional scopes with explicit daily 2027 coverage."""
    data = template_fixture()
    for shard in data["shards"]:
        if shard["venue"] not in {"SHFE", "DCE"}:
            continue
        first = date(2026, 12, 31)
        days = [first + timedelta(days=n) for n in range(366)]
        shard["coverage_end"] = "2027-12-31"
        shard["layers"]["trading_days"] = [
            {
                "venue": shard["venue"],
                "day": day.isoformat(),
                "status": "open" if day in (date(2027, 1, 1), OWNER) else "closed",
                "source_ids": [SOURCE["id"]],
            }
            for day in days
        ]
        for row in shard["layers"]["trading_hours"]:
            row["valid_to"] = "2027-12-31"
        for requirement in shard["requirements"]:
            requirement["end_day"] = "2027-12-31"
    return data


def cli(env, *args):
    proc = subprocess.run(
        [sys.executable, "-m", "bt_api_py.trading_calendar_observation_run", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    return proc.returncode, [json.loads(line) for line in proc.stdout.splitlines()], proc.stderr


def make_coordinator(store, gate):
    return SimulationCalendarEpochCoordinator(
        mode="SIMULATION_ONLY",
        store=store,
        snapshot_factory=CalendarSnapshot,
        gate=gate,
        route="route",
        source_generation="generation",
        trading_day=date(2027, 1, 1),
        last_sequence=1,
        mappings=MAPPINGS,
        closure=SimulationEpochClosure(
            datetime(2027, 1, 1, 13, tzinfo=UTC),
            datetime(2027, 1, 4, 4, tzinfo=UTC),
            date(2027, 1, 1),
            date(2027, 1, 7),
            3,
            0,
        ),
        required_instruments=("sim.shfe", "sim.dce"),
        required_years=(("SHFE", 2027), ("DCE", 2027)),
        initial_pending="DECLARED_NO_PRIOR_PENDING",
    )


def test_reserved_partial_manual_batch_then_gated_prepared_epoch(tmp_path):
    root, config = settings(tmp_path, "manual")
    replacement = (
        "from tests.unit.test_trading_calendar_daily_batch_epoch_integration import annual_fixture\n"
        "mock.build = lambda year: (annual_fixture(), {'sources': [mock.SOURCE], "
        "'trading_days': [annual_fixture()['shards'][0]['layers']['trading_days'][0]], "
        "'trading_hours': [], 'exceptions': []})"
    )
    inject = "\n".join(
        replacement if line.startswith("mock.build = lambda year:") else line
        for line in FAKE_BATCH.splitlines()
    )
    env = env_without_network(tmp_path, inject)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    env["FAIL_DCE"] = "1"
    env["FAKE_AK_END"] = "10:30"
    args = list(run_args(root, config))
    args[args.index("--budget-seconds") + 1] = "120"
    rc, initial, err = cli(env, *args)
    assert rc == 10, (initial, err)
    run_id = next(row["run_id"] for row in initial if row.get("candidate_state") == "COMMITTED")
    assert confirm(env, root, initial[-1])["status"] == "COMMITTED"
    (root / "supplement.yaml").write_text(
        json.dumps(
            {
                "sources": [SOURCE],
                "trading_days": [annual_fixture()["shards"][0]["layers"]["trading_days"][0]],
                "trading_hours": [],
                "exceptions": [],
            }
        )
    )
    store = TradingCalendarStore(root)
    gate = Gate()
    coord = make_coordinator(store, gate)
    old = coord.current_epoch()
    uncertain_gate = Gate("bad")
    uncertain = make_coordinator(store, uncertain_gate)
    uncertain_old = uncertain.current_epoch()
    old_current = (root / "CURRENT.json").read_bytes()
    items = [scope("DCE"), scope("SHFE")]
    for item in items:
        item["target"]["valid_to"] = "2027-12-31"
    ids = bundle(items)
    ids["original_run_id"] = run_id
    script = """
import json,sys
from datetime import date
from bt_api_py import trading_calendar_daily_batch as b
result=b.run(root=sys.argv[1],run_id=sys.argv[2],scopes=json.loads(sys.argv[3]),
             identity_bundle=json.loads(sys.argv[4]),requested_date=date(2026,11,13))
print(json.dumps(result))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script, str(root), run_id, json.dumps(items), json.dumps(ids)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    pending = json.loads(proc.stdout.splitlines()[-1])
    assert pending["status"] == "PENDING_REVIEW" and pending["batch_status"] == "PARTIAL", pending
    observations = {row["target"]["venue"]: row for row in pending["observations"]}
    assert observations["SHFE"]["disposition"] == "UPDATED_AKSHARE"
    assert observations["DCE"]["disposition"] == "MOCK_FALLBACK"
    assert observations["DCE"]["observation_status"] == "FETCH_ERROR"
    last_success = {
        row["target"]["venue"]: row["last_success"]["status"]
        for row in pending["scope_checks"]["scopes"]
    }
    assert last_success == {"SHFE": "VISIBLE_UNAUTHENTICATED", "DCE": "UNKNOWN"}
    assert (root / "CURRENT.json").read_bytes() == old_current and coord.current_epoch() is old
    assert old.expected_days.expected_trading_days("SHFE", 2027) == (date(2027, 1, 1), OWNER)
    assert (
        old.schedule.session_state("sim.shfe", datetime(2027, 1, 4, 1, 30, tzinfo=UTC)).phase.value
        == "continuous"
    )
    assert confirm(env, root, pending)["status"] == "COMMITTED"
    assert store.read_current().version_id != old.calendar_ref and coord.current_epoch() is old

    event = SimulationEpochEvent("switch", "route", "generation", OWNER, 2)
    unknown = uncertain.switch(event)
    assert (unknown.outcome, unknown.reason) == ("PENDING_UNKNOWN", "GATE_UNKNOWN")
    assert uncertain.current_epoch() is uncertain_old
    assert uncertain_old.calendar_ref == old.calendar_ref
    assert uncertain.switch(event).outcome == "PENDING_UNKNOWN" and len(uncertain_gate.calls) == 1
    result = coord.switch(event)
    assert result.outcome == "COMMITTED", result
    new = coord.current_epoch()
    assert old.calendar_ref != new.calendar_ref == store.read_current().version_id
    assert new.version_sha256 == new.view.status().version_sha256 == result.candidate_version_sha256
    # The dates port has no public reference field. Inspect the actual prepared
    # adapter's shared binding to distinguish equal V1/V2 date lists; this is
    # an internal implementation check, not external identity authentication.
    for epoch in (old, new):
        shared = epoch.expected_days._shared
        assert shared is epoch.schedule._shared
        assert shared.view is epoch.view
        assert shared.identity.calendar_ref == epoch.calendar_ref
        assert shared.identity.version_sha256 == epoch.version_sha256
        assert shared.identity.source_authentication == "NOT_PROVEN"
    assert old.expected_days._shared is not new.expected_days._shared
    for venue in ("SHFE", "DCE"):
        assert new.expected_days.expected_trading_days(venue, 2027) == (date(2027, 1, 1), OWNER)
        query = new.view.query(
            venue=venue,
            kind="future",
            product="pfuture",
            contract=f"mock_{venue.lower()}_future_contract",
            trading_day=OWNER,
        )
        assert query.result.status == "COMPLETE" and query.result.calendar_ref == new.calendar_ref
        attribution = query.simulation_provenance or query.declared_mock_provenance
        assert attribution is not None
        assert query.result.sources_verified is False
        provenance_by_id = {row["window_id"]: row for row in attribution["windows"]}
        assert len(provenance_by_id) == len(attribution["windows"]) == len(query.result.windows)
        for instant, expected_scope in (
            (datetime(2027, 1, 1, 13, 30, tzinfo=UTC), "NIGHT"),
            (datetime(2027, 1, 4, 1, 30, tzinfo=UTC), "DAY"),
        ):
            state = new.schedule.session_state(f"sim.{venue.lower()}", instant)
            assert state.phase.value == "continuous" and state.trading_day == OWNER
            assert state.calendar_ref == new.calendar_ref
            windows = new.schedule.windows(
                f"sim.{venue.lower()}", instant, instant + timedelta(minutes=5)
            )
            assert len(windows) == 1 and windows[0] == state.current_window
            original = [w for w in query.result.windows if w.window_id == windows[0].session_id]
            assert len(original) == 1
            assert (original[0].start_utc, original[0].end_utc, original[0].phase) == (
                windows[0].start_utc,
                windows[0].end_utc,
                windows[0].kind.value,
            )
            provenance = provenance_by_id[windows[0].session_id]
            assert provenance["scope"] == expected_scope
            expected_source = "AKSHARE" if venue == "SHFE" and expected_scope == "DAY" else "MOCK"
            assert provenance["source_type"] == expected_source
            if venue == "SHFE":
                basis = (
                    "AKSHARE_SYNTAX_CANDIDATE"
                    if expected_source == "AKSHARE"
                    else "MOCK_ASSUMPTION"
                )
                assert provenance["fields"]["scope"] == {"value": expected_scope, "basis": basis}
                assert provenance["simulation"]["phase"] == {
                    "value": "continuous",
                    "basis": "MOCK_ASSUMPTION",
                }
            else:
                assert provenance["source_authentication"] == "NOT_PROVEN"
    assert old.calendar_ref == old.view.status().calendar_ref
    after_original_end = datetime(2027, 1, 4, 2, 15, tzinfo=UTC)
    assert old.schedule.session_state("sim.shfe", after_original_end).phase.value != "continuous"
    assert new.schedule.session_state("sim.shfe", after_original_end).phase.value == "continuous"
    assert new.schedule.session_state("sim.dce", after_original_end).phase.value != "continuous"
    assert (
        new.schedule.session_state("sim.shfe", datetime(2027, 1, 4, 2, 30, tzinfo=UTC)).phase.value
        != "continuous"
    )
