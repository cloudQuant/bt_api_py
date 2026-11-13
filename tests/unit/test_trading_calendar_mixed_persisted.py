"""Offline mixed simulation using real local calendar storage."""

import json
import subprocess
import sys
from datetime import UTC, date, datetime

import pytest

from bt_api_py import trading_calendar_mixed_publication as mixed_pub
from bt_api_py import trading_calendar_mixed_simulation_version as mixed
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from tests.unit.test_trading_calendar_mixed_simulation_version import capture
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation
from tests.unit.test_trading_calendar_observation_run import env_without_network

FS = "TRUSTED_LOCAL_POSIX"


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_store_mixed_version_and_same(tmp_path, mode):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"calendars": {"root": str(root), "review_mode": mode}}))
    env = env_without_network(
        tmp_path,
        """
from tests.unit.test_trading_calendar_mixed_simulation_version import template_fixture
from bt_api_py import trading_calendar_mock as mock
mock.build = lambda year: (template_fixture(), {'sources': [mock.SOURCE], 'trading_days': [template_fixture()['shards'][0]['layers']['trading_days'][0]], 'trading_hours': [], 'exceptions': []})
""",
    )
    code = """
import json, sys
from bt_api_py import trading_calendar_observation_run as runs
result = runs.run_once(root=sys.argv[1], config=sys.argv[2], supplement=None, budget_seconds=30,
    enable_shfe=False, filesystem='TRUSTED_LOCAL_POSIX', run_id='a'*32, capture_id='b'*32,
    parent_id='c'*32, derived_id='d'*32, mock_year=2026, emit=lambda _: None)
print(json.dumps(result))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(root), str(config)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["candidate_state"] == "COMMITTED", str(result)
    operator = MockCalendarPublication(root=str(root), run_id="a" * 32)
    assert operator.initialize(mode=mode)["status"] == "INITIALIZED"
    capture(root, "e" * 32, "a" * 32, "日盘 09:00-10:00")
    record = mixed.build_template(
        root=str(root),
        candidate_root=str(root),
        filesystem=FS,
        candidate_id="d" * 32,
        observation_id="e" * 32,
        run_id="a" * 32,
        target={
            "venue": "SHFE",
            "kind": "future",
            "product": "pfuture",
            "mock_contract": "mock_shfe_future_contract",
            "valid_from": "2026-12-31",
            "valid_to": "2027-01-04",
        },
        assumptions={
            "DAY": {"phase": "continuous", "timezone": "Asia/Shanghai"},
            "NIGHT": {"phase": "continuous", "timezone": "Asia/Shanghai"},
        },
    )
    evidence = mixed.template_digest(
        record, root=str(root), candidate_root=str(root), filesystem=FS, include_trace=True
    )
    built = mixed_pub.build_once(
        root=str(root),
        filesystem=FS,
        candidate_id="f" * 32,
        template=record,
        evidence_sha256=evidence,
    )
    assert built["status"] == "COMMITTED", built
    proposal = operator.prepare(candidate_id="f" * 32)
    assert proposal["status"] == "FIRST_SIMULATION_PUBLICATION", proposal
    confirmation = (
        {"reviewer": "MOCK_reviewer", "confirmed_at": "2026-11-13T00:00:00.000000Z"}
        if mode == "manual"
        else {}
    )
    committed = operator.publish(
        proposal=proposal,
        publication_id="MOCK_pub",
        version_id="MOCK_ver",
        emit=lambda _: None,
        **confirmation,
    )
    assert committed["status"] == "COMMITTED", str(committed)
    assert committed["data_origin"] == "AKSHARE_MOCK"
    current = operator._store.read_current()
    assert current.simulation_provenance["windows"][0]["source_type"] == "AKSHARE"
    assert current.simulation_provenance["windows"][1]["source_type"] == "MOCK"
    same = operator.prepare(candidate_id="f" * 32)
    assert same["status"] == "SAME", same
    no_change = operator.publish(
        proposal=same, publication_id="MOCK_pub2", version_id="MOCK_ver2", emit=lambda _: None
    )
    assert no_change["status"] == "SAME", no_change
    assert no_change["data_origin"] == "AKSHARE_MOCK"
    assert operator._store.read_current().version_id == "MOCK_ver"


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_mixed_update_cli_auto(tmp_path, mode):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"calendars": {"root": str(root), "review_mode": mode}}))
    env = env_without_network(
        tmp_path,
        """
from tests.unit.test_trading_calendar_mixed_simulation_version import template_fixture
from bt_api_py import trading_calendar_mock as mock
mock.build = lambda year: (template_fixture(), {'sources': [mock.SOURCE], 'trading_days': [template_fixture()['shards'][0]['layers']['trading_days'][0]], 'trading_hours': [], 'exceptions': []})
""",
    )
    code = """
import json, sys
from bt_api_py import trading_calendar_observation_run as runs
r = runs.run_once(root=sys.argv[1], config=sys.argv[2], supplement=None, budget_seconds=30,
 enable_shfe=False, filesystem='TRUSTED_LOCAL_POSIX', run_id='a'*32, capture_id='b'*32,
 parent_id='c'*32, derived_id='d'*32, mock_year=2026, emit=lambda _:None)
print(json.dumps(r))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(root), str(config)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["candidate_state"] == "COMMITTED"
    operator = MockCalendarPublication(root=str(root), run_id="a" * 32)
    assert operator.initialize(mode=mode)["status"] == "INITIALIZED"
    from bt_api_py.trading_calendar_mock import SOURCE
    from tests.unit.test_trading_calendar_mixed_simulation_version import template_fixture

    supplement = {
        "sources": [SOURCE],
        "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    (root / "supplement.yaml").write_text(json.dumps(supplement))
    capture(root, "e" * 32, "a" * 32, "日盘 09:00-10:00")
    flags = (
        "--mixed-observation-id",
        "e" * 32,
        "--mixed-observation-run-id",
        "a" * 32,
        "--mixed-venue",
        "SHFE",
        "--mixed-product",
        "pfuture",
        "--mixed-contract",
        "mock_shfe_future_contract",
        "--mixed-valid-from",
        "2026-12-31",
        "--mixed-valid-to",
        "2027-01-04",
        "--mixed-day-phase",
        "continuous",
        "--mixed-night-phase",
        "continuous",
        "--mixed-timezone",
        "Asia/Shanghai",
        "--mixed-alignment",
        "REPEAT_OVER_MOCK_OWNER_DAY_MAPPING",
    )
    rc, rows, err = command(env, *operation(root, "a" * 32, "mock-update", *flags))
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    if mode == "manual":
        pending = rows[-1]
        assert pending["status"] == "PENDING_REVIEW"
        assert pending["data_origin"] == "AKSHARE_MOCK"
        rc, confirmation, err = command(
            env,
            *operation(
                root,
                pending["run_id"],
                "mock-confirm",
                "--review-id",
                pending["review_id"],
                "--review-sha256",
                pending["review_payload_sha256"],
                "--reviewer",
                "MOCK_reviewer",
                "--confirmed-at",
                "2026-11-13T00:00:00.000000Z",
            ),
        )
        assert rc == 0, (confirmation, err)
        assert confirmation[-1]["status"] == "COMMITTED"
        assert confirmation[-1]["data_origin"] == "AKSHARE_MOCK"
    else:
        assert rows[-1]["status"] == "COMMITTED"
    assert rows[-1]["observation_status"] == "RETAINED_OBSERVATION_REUSED_NO_NEW_FETCH"
    assert operator._store.read_current().simulation_provenance is not None
    rc, shown, err = command(env, *operation(root, "a" * 32, "mock-show"))
    assert rc == 0 and shown[-1]["data_origin"] == "AKSHARE_MOCK", (shown, err)
    assert shown[-1]["current"]["data_origin"] == "AKSHARE_MOCK"
    bound = operator.binding(trading_day=date(2027, 1, 1))
    scope = {
        "venue": "SHFE",
        "kind": "future",
        "product": "pfuture",
        "contract": "mock_shfe_future_contract",
        "trading_day": date(2027, 1, 4),
    }
    initial = bound.query(**scope)
    assert initial["data_origin"] == "AKSHARE_MOCK"
    query = initial["query"]
    assert query.result.status == "COMPLETE"
    provenance = query.simulation_provenance
    assert provenance["evidence_locator"]["observation_id"] == "e" * 32
    assert provenance["evidence_locator"]["observation_run_id"] == "a" * 32
    day = next(w for w in provenance["windows"] if w["source_type"] == "AKSHARE")
    night = next(w for w in provenance["windows"] if w["source_type"] == "MOCK")
    assert day["fields"]["start"] == {"value": "09:00", "basis": "AKSHARE_SYNTAX_CANDIDATE"}
    assert day["simulation"]["phase"] == {"value": "continuous", "basis": "MOCK_ASSUMPTION"}
    assert day["observation"]["phase"] == day["observation"]["identity"] == "UNKNOWN"
    assert night["fields"]["start"]["basis"] == "MOCK_ASSUMPTION"
    night_window = next(w for w in query.result.windows if w.window_id == night["window_id"])
    assert night_window.start_local.date() == date(2027, 1, 1)
    assert night_window.contains(night_window.start_utc)
    assert not night_window.contains(night_window.end_utc)
    missing = bound.query(**{**scope, "contract": "mock_unknown"})["query"]
    assert (
        missing.result.status in {"UNKNOWN", "OUT_OF_SCOPE"}
        and missing.simulation_provenance is None
    )
    # The separate UTC port cannot carry field-level provenance yet and must
    # not project /3 as though whole-row source IDs were per-window evidence.
    from bt_api_base.trading_calendar_protocol import (
        CalendarContractKind,
        CalendarCoverageStatus,
        CalendarQuery,
        CalendarScope,
        UtcInterval,
    )

    from bt_api_py.trading_calendar_query_simulation import SimulationCalendarQuery

    utc_query = SimulationCalendarQuery(mode="SIMULATION_ONLY", store=operator._store, closure=None)
    request = CalendarQuery(
        CalendarScope(
            "MOCK.route",
            "SHFE",
            CalendarContractKind.FUTURE,
            "pfuture",
            "mock_shfe_future_contract",
        ),
        UtcInterval(
            datetime(2027, 1, 4, 1, tzinfo=UTC),
            datetime(2027, 1, 4, 2, tzinfo=UTC),
        ),
    )
    assert utc_query.query(request).status is CalendarCoverageStatus.UNKNOWN
    before = (root / "CURRENT.json").read_bytes()
    rc, unselected, err = command(env, *operation(root, "a" * 32, "mock-update"))
    assert rc != 0 and unselected[-1]["reason"] == "MIXED_SCOPE_REQUIRED_FOR_EXISTING_VERSION", (
        unselected,
        err,
    )
    assert (root / "CURRENT.json").read_bytes() == before
    rc, same, err = command(env, *operation(root, "a" * 32, "mock-update", *flags))
    assert rc == 10 and same[-1]["status"] == "SAME", str((same[-1], err))
    assert same[-1]["data_origin"] == "AKSHARE_MOCK"
    assert (root / "CURRENT.json").read_bytes() == before
    capture(root, "9" * 32, "a" * 32, "日盘 09:00-10:30")
    changed_flags = (flags[0], "9" * 32, *flags[2:])
    rc, changed, err = command(env, *operation(root, "a" * 32, "mock-update", *changed_flags))
    assert rc == (0 if mode == "auto" else 10), (changed, err)
    if mode == "manual":
        pending = changed[-1]
        assert (
            pending["proposal"]["difference"]["schema"]
            == "bt_api.calendar-full-declaration-difference/2"
        )
        from bt_api_py import trading_calendar_detail_collection as storage

        evidence_path = root / "review-bundles" / f"mock-mixed-{pending['candidate_id']}.json"
        raw = evidence_path.read_bytes()
        body, _ = storage._unwrap(raw)
        body["template"]["trace"]["response_digest"] = "0" * 64
        evidence_path.write_bytes(storage._envelope(body))
        rc, confirmation, err = command(
            env,
            *operation(
                root,
                pending["run_id"],
                "mock-confirm",
                "--review-id",
                pending["review_id"],
                "--review-sha256",
                pending["review_payload_sha256"],
                "--reviewer",
                "MOCK_reviewer",
                "--confirmed-at",
                "2026-11-13T00:00:00.000000Z",
            ),
        )
        assert rc != 0 and confirmation[-1]["status"] == "UNKNOWN", (confirmation, err)
        assert (root / "CURRENT.json").read_bytes() == before
        evidence_path.write_bytes(raw)
        rc, repeated, err = command(
            env,
            *operation(
                root,
                pending["run_id"],
                "mock-confirm",
                "--review-id",
                pending["review_id"],
                "--review-sha256",
                pending["review_payload_sha256"],
                "--reviewer",
                "MOCK_reviewer",
                "--confirmed-at",
                "2026-11-13T00:00:00.000000Z",
            ),
        )
        assert rc != 0 and repeated[-1]["reason"] == "CONFIRMATION_RECONCILE_FIRST", (repeated, err)
        rc, new_review, err = command(
            env, *operation(root, "a" * 32, "mock-update", *changed_flags)
        )
        assert rc == 10 and new_review[-1]["status"] == "PENDING_REVIEW", (new_review, err)
        pending = new_review[-1]
        rc, confirmation, err = command(
            env,
            *operation(
                root,
                pending["run_id"],
                "mock-confirm",
                "--review-id",
                pending["review_id"],
                "--review-sha256",
                pending["review_payload_sha256"],
                "--reviewer",
                "MOCK_reviewer",
                "--confirmed-at",
                "2026-11-13T00:00:00.000000Z",
            ),
        )
        assert rc == 0, (confirmation, err)
    assert (root / "CURRENT.json").read_bytes() != before
    still_old = bound.query(**scope)["query"]
    assert still_old.binding.calendar_ref == query.binding.calendar_ref
    assert (
        next(
            w for w in still_old.result.windows if w.window_id == day["window_id"]
        ).end_local.minute
        == 0
    )
    switched = bound.activate(trading_day=date(2027, 1, 4), sequence=1, event_id="MOCK_day_switch")
    assert switched["activation"].outcome == "ACTIVATED"
    switched_query = bound.query(**scope)["query"]
    assert switched_query.binding.calendar_ref != query.binding.calendar_ref
    assert (
        next(
            w for w in switched_query.result.windows if w.window_id == day["window_id"]
        ).end_local.minute
        == 30
    )
    restarted = MockCalendarPublication(root=str(root), run_id="a" * 32).binding(
        trading_day=date(2027, 1, 4)
    )
    restarted_query = restarted.query(**scope)["query"]
    assert restarted_query.binding.calendar_ref == switched_query.binding.calendar_ref
    assert restarted_query.simulation_provenance["evidence_locator"]["observation_id"] == "9" * 32
