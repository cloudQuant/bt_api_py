"""Fresh, explicitly requested simulated updates using an offline AKShare double."""

import json
import subprocess
import sys

import pytest

from bt_api_py.trading_calendar_mock import SOURCE
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.unit.test_trading_calendar_mixed_simulation_version import template_fixture
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation, run_args, settings
from tests.unit.test_trading_calendar_observation_run import env_without_network

FAKE = r"""
import os
from datetime import date
from tests.unit.test_trading_calendar_mixed_simulation_version import template_fixture
from bt_api_py import trading_calendar_mock as mock
from bt_api_py import trading_calendar_mock_cli as mock_cli
from bt_api_py import trading_calendar_akshare_observation as observation
from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract
mock.build = lambda year: (template_fixture(), {'sources': [mock.SOURCE], 'trading_days': [template_fixture()['shards'][0]['layers']['trading_days'][0]], 'trading_hours': [], 'exceptions': []})
mock_cli._beijing_today = lambda: date(2026, 11, 13)
original_observe = observation.observe
def fake_observe(**kwargs):
    def collect(**options):
        from pathlib import Path
        assert (Path(kwargs['root']) / 'observation-runs' / ('mock-mixed-request-' + kwargs['observation_id'] + '.json')).is_file()
        with open(os.environ['FAKE_CALLS'], 'a') as file:
            file.write('call\n')
        status = os.environ.get('FAKE_AK_STATUS', 'OK')
        if status == 'TIMEOUT':
            runner = lambda *_: ('CHILD_TIMEOUT', None)
        elif status == 'EMPTY':
            runner = lambda *_: ('CHILD_OK', {'status': 'OK', 'rows': []})
        elif status == 'MISSING':
            runner = lambda *_: ('CHILD_OK', {'status': 'OK', 'rows': [['名称', '模拟']]})
        elif status == 'ERROR':
            runner = lambda *_: ('CHILD_OK', {'status': 'FETCH_ERROR', 'rows': []})
        else:
            runner = lambda *_: ('CHILD_OK', {'status': 'OK', 'rows': [['交易时间', '日盘 09:00-' + os.environ.get('FAKE_AK_END', '10:00')]]})
        return collect_akshare_contract(**options, _runner=runner)
    result = original_observe(**kwargs, _collector=collect)
    if os.environ.get('FAKE_AK_STATUS') == 'AFTER_WRITE_UNKNOWN':
        return {**result, 'record_state': 'COMMIT_UNKNOWN'}
    return result
observation.observe = fake_observe
"""


def flags():
    return (
        "--mixed-fetch-contract",
        "RB2701",
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


def confirm(env, root, pending):
    rc, rows, err = command(
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
    assert rc == 0, (rows, err)
    return rows[-1]


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_fresh_observation_changes_same_and_fallback(tmp_path, mode):
    root, config = settings(tmp_path, mode)
    env = env_without_network(tmp_path, FAKE)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    rc, rows, err = command(env, *run_args(root, config))
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    original_run = next(row["run_id"] for row in rows if row.get("candidate_state") == "COMMITTED")
    if mode == "manual":
        assert confirm(env, root, rows[-1])["status"] == "COMMITTED"
    supplement = {
        "sources": [SOURCE],
        "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    (root / "supplement.yaml").write_text(json.dumps(supplement))

    def update():
        return command(env, *operation(root, original_run, "mock-update", *flags()))

    rc, rows, err = update()
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    first = rows[-1]
    assert first["observation_status"] == "NEW_OBSERVATION_CAPTURED"
    assert first["observation"]["observation_status"] == "SUCCESS"
    assert first["observation"]["request_state"] == "COMMITTED"
    assert first["observation"]["requested_date"] == "2026-11-13"
    assert first["observation"]["date_role"] == "REQUEST_DATE_NOT_EFFECTIVE_DATE"
    assert first["observation"]["run_id"] == first["run_id"]
    if mode == "manual":
        assert confirm(env, root, first)["status"] == "COMMITTED"
    store = TradingCalendarStore(root)
    first_current = (root / "CURRENT.json").read_bytes()
    assert (
        store.read_current().simulation_provenance["evidence_locator"]["observation_id"]
        == first["observation"]["observation_id"]
    )

    env["FAKE_AK_STATUS"] = "ERROR"
    for key, value in (
        ("--mixed-product", "another_product"),
        ("--mixed-valid-from", "2027-01-01"),
        ("--mixed-valid-to", "2027-01-03"),
    ):
        different = list(flags())
        different[different.index(key) + 1] = value
        rc, rejected, err = command(env, *operation(root, original_run, "mock-update", *different))
        assert rc != 0 and rejected[-1]["reason"] == "MIXED_SCOPE_CONFLICT", (rejected, err)
        assert rejected[-1]["observation_state"] == "NOT_ATTEMPTED"
        allocated = next(row for row in rejected if row.get("event") == "MOCK_UPDATE_IDS_ALLOCATED")
        assert not (root / "observation-runs" / f"mock-{allocated['new_run_id']}.json").exists()
        assert (root / "CURRENT.json").read_bytes() == first_current
        assert len((tmp_path / "calls").read_text().splitlines()) == 1
    del env["FAKE_AK_STATUS"]

    rc, rows, err = update()
    same = rows[-1]
    assert rc == 10 and same["status"] == "SAME", (rows, err)
    assert same["observation"]["observation_id"] != first["observation"]["observation_id"]
    assert (root / "CURRENT.json").read_bytes() == first_current

    env["FAKE_AK_END"] = "10:30"
    rc, rows, err = update()
    changed = rows[-1]
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    if mode == "manual":
        assert changed["status"] == "PENDING_REVIEW"
        assert confirm(env, root, changed)["status"] == "COMMITTED"
    else:
        assert changed["status"] == "COMMITTED"
    current = (root / "CURRENT.json").read_bytes()
    assert current != first_current
    assert (
        store.read_current().simulation_provenance["evidence_locator"]["observation_id"]
        == changed["observation"]["observation_id"]
    )
    for status, expected in (
        ("EMPTY", "EMPTY"),
        ("MISSING", "MISSING_TRADING_TIME"),
        ("ERROR", "FETCH_ERROR"),
        ("TIMEOUT", "FETCH_TIMEOUT"),
    ):
        env["FAKE_AK_STATUS"] = status
        rc, rows, err = update()
        outcome = rows[-1]
        assert rc != 0 and outcome["observation"]["observation_status"] == expected, (rows, err)
        assert outcome["publication"] == "NOT_ATTEMPTED"
        assert outcome["fallback"] == "EXISTING_CURRENT_NOT_REPLACED_BY_THIS_RUN"
        assert (root / "CURRENT.json").read_bytes() == current
    assert len((tmp_path / "calls").read_text().splitlines()) == 7
    env["FAKE_AK_STATUS"] = "AFTER_WRITE_UNKNOWN"
    rc, rows, err = update()
    uncertain = rows[-1]
    assert rc != 0 and uncertain["status"] == "UNKNOWN", (rows, err)
    assert uncertain["observation"]["observation_state"] == "COMMIT_UNKNOWN"
    assert (root / "CURRENT.json").read_bytes() == current
    rc, shown, err = command(
        env,
        *operation(
            root,
            uncertain["run_id"],
            "mock-show",
            "--akshare-observation-id",
            uncertain["observation"]["observation_id"],
        ),
    )
    assert rc == 0 and shown[-1]["akshare_candidate"]["status"] == "VISIBLE_UNAUTHENTICATED", (
        shown,
        err,
    )
    request = shown[-1]["fresh_request"]
    assert request["status"] == "VISIBLE_UNAUTHENTICATED"
    assert request["request"]["target"]["mock_contract"] == "mock_shfe_future_contract"
    assert request["request"]["request"]["contract"] == "RB2701"
    assert request["request"]["request"]["requested_date"] == "2026-11-13"
    assert len((tmp_path / "calls").read_text().splitlines()) == 8
    rc, rows, err = command(
        env,
        *operation(
            root, original_run, "mock-update", *flags(), "--mixed-fetch-budget", "0.000000001"
        ),
    )
    assert rc != 0 and rows[-1]["observation"]["observation_state"] == "NOT_ATTEMPTED", (rows, err)
    assert (root / "CURRENT.json").read_bytes() == current
    assert len((tmp_path / "calls").read_text().splitlines()) == 8


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_failed_first_observation_publishes_only_labeled_mock(tmp_path, mode):
    root, config = settings(tmp_path, mode)
    env = env_without_network(tmp_path, FAKE)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    env["FAKE_AK_STATUS"] = "ERROR"
    script = """
import json, sys
from bt_api_py import trading_calendar_observation_run as runs
r = runs.run_once(root=sys.argv[1], config=sys.argv[2], supplement=None, budget_seconds=30,
 enable_shfe=False, filesystem='TRUSTED_LOCAL_POSIX', run_id='a'*32, capture_id='b'*32,
 parent_id='c'*32, derived_id='d'*32, mock_year=2026, emit=lambda _:None)
print(json.dumps(r))
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(root), str(config)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (
        process.returncode == 0 and json.loads(process.stdout)["candidate_state"] == "COMMITTED"
    ), process.stderr
    operator = MockCalendarPublication(root=str(root), run_id="a" * 32)
    assert operator.initialize(mode=mode)["status"] == "INITIALIZED"
    supplement = {
        "sources": [SOURCE],
        "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    (root / "supplement.yaml").write_text(json.dumps(supplement))
    rc, rows, err = command(env, *operation(root, "a" * 32, "mock-update", *flags()))
    outcome = rows[-1]
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    assert outcome["observation"]["observation_status"] == "FETCH_ERROR"
    assert outcome["fallback"] == "FIRST_PUBLICATION_MOCK_ONLY"
    assert outcome["data_origin"] == "MOCK"
    if mode == "manual":
        assert not (root / "CURRENT.json").exists()
        assert confirm(env, root, outcome)["status"] == "COMMITTED"
    assert operator._store.read_current().simulation_provenance is None
    assert len((tmp_path / "calls").read_text().splitlines()) == 1
