"""Offline end-to-end daily batch on the real simulation store."""

import json
from datetime import date

import pytest

from bt_api_py.trading_calendar_mock import SOURCE
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.unit.test_trading_calendar_mixed_simulation_version import template_fixture
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_fresh_observation import FAKE, confirm
from tests.unit.test_trading_calendar_mock_persistent import operation, run_args, settings
from tests.unit.test_trading_calendar_observation_run import env_without_network

FAKE_BATCH = "\n".join(
    line for line in FAKE.splitlines() if "assert (Path(kwargs['root'])" not in line
)
FAKE_BATCH = FAKE_BATCH.replace(
    "status = os.environ.get('FAKE_AK_STATUS', 'OK')",
    "status = 'ERROR' if os.environ.get('FAIL_DCE') and options['venue'] == 'DCE' else os.environ.get('FAKE_AK_STATUS', 'OK')",
)


def scope(venue):
    return {
        "contract": "RB2701" if venue == "SHFE" else "M2701",
        "target": {
            "venue": venue,
            "kind": "future",
            "product": "pfuture",
            "mock_contract": f"mock_{venue.lower()}_future_contract",
            "valid_from": "2026-12-31",
            "valid_to": "2027-01-04",
        },
        "assumptions": {
            "DAY": {"phase": "continuous", "timezone": "Asia/Shanghai"},
            "NIGHT": {"phase": "continuous", "timezone": "Asia/Shanghai"},
        },
        "alignment": "REPEAT_OVER_MOCK_OWNER_DAY_MAPPING",
        "timeout": 2,
    }


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_daily_batch_one_review_one_publication_and_same(tmp_path, mode):
    root, config = settings(tmp_path, mode)
    env = env_without_network(tmp_path, FAKE_BATCH)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    rc, initial, err = command(env, *run_args(root, config))
    assert rc == (0 if mode == "auto" else 10), (initial, err)
    original_run = next(
        row["run_id"] for row in initial if row.get("candidate_state") == "COMMITTED"
    )
    if mode == "manual":
        assert confirm(env, root, initial[-1])["status"] == "COMMITTED"
    supplement = {
        "sources": [SOURCE],
        "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    (root / "supplement.yaml").write_text(json.dumps(supplement))
    baseline = (root / "CURRENT.json").read_bytes()
    before_publications = set((root / "publications").iterdir())
    args = operation(
        root,
        original_run,
        "mock-batch",
        "--scope-json",
        json.dumps(scope("SHFE")),
        "--scope-json",
        json.dumps(scope("DCE")),
    )
    rc, rows, err = command(env, *args)
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    result = rows[-1]
    assert len(result["observations"]) == 2
    assert len({item["observation_id"] for item in result["observations"]}) == 2
    assert all(item["observation_status"] == "SUCCESS" for item in result["observations"])
    assert result["date_role"] == "REQUEST_DATE_NOT_EFFECTIVE_DATE"
    if mode == "manual":
        assert result["status"] == "PENDING_REVIEW"
        assert (root / "CURRENT.json").read_bytes() == baseline
        assert confirm(env, root, result)["status"] == "COMMITTED"
    else:
        assert result["status"] == "COMMITTED"
    assert len(set((root / "publications").iterdir()) - before_publications) == 1
    store = TradingCalendarStore(root)
    current = store.read_current()
    assert len(current.simulation_provenance["update_targets"]) == 2
    assert (
        json.loads((root / "versions" / f"{current.version_id}.json").read_bytes())["schema"]
        == "bt_api.calendar-store.snapshot/5"
    )
    operator = MockCalendarPublication(root=str(root), run_id=original_run)
    bound = operator.binding(trading_day=date(2027, 1, 4))
    for venue in ("SHFE", "DCE"):
        query = bound.query(
            venue=venue,
            kind="future",
            product="pfuture",
            contract=f"mock_{venue.lower()}_future_contract",
            trading_day=date(2027, 1, 4),
        )["query"]
        assert query.result.status == "COMPLETE"
        assert query.simulation_provenance["target"]["venue"] == venue
    rc, shown, err = command(
        env, *operation(root, original_run, "mock-batch-show", "--batch-id", result["batch_id"])
    )
    assert rc == 0 and shown[-1]["status"] == "VISIBLE_UNAUTHENTICATED", (shown, err)
    assert len(shown[-1]["evidence"]["observations"]) == 2
    committed = (root / "CURRENT.json").read_bytes()
    rc, repeated, err = command(env, *args)
    assert rc == 10 and repeated[-1]["status"] == "SAME", (repeated, err)
    assert (root / "CURRENT.json").read_bytes() == committed
    assert len(set((root / "publications").iterdir()) - before_publications) == 1


def test_one_failed_scope_records_both_and_does_not_publish(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, FAKE_BATCH)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    rc, initial, err = command(env, *run_args(root, config))
    assert rc == 0, (initial, err)
    run_id = next(row["run_id"] for row in initial if row.get("candidate_state") == "COMMITTED")
    supplement = {
        "sources": [SOURCE],
        "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    (root / "supplement.yaml").write_text(json.dumps(supplement))
    current = (root / "CURRENT.json").read_bytes()
    env["FAIL_DCE"] = "1"
    args = operation(
        root,
        run_id,
        "mock-batch",
        "--scope-json",
        json.dumps(scope("SHFE")),
        "--scope-json",
        json.dumps(scope("DCE")),
    )
    rc, rows, err = command(env, *args)
    result = rows[-1]
    assert rc != 0 and result["reason"] == "BATCH_INCOMPLETE", (rows, err)
    assert [row["observation_status"] for row in result["observations"]] == [
        "SUCCESS",
        "FETCH_ERROR",
    ]
    assert all(
        row["source_type"] == "AKSHARE" and row["source_authentication"] == "NOT_PROVEN"
        for row in result["observations"]
    )
    assert result["publication"] == "NOT_ATTEMPTED"
    assert (root / "CURRENT.json").read_bytes() == current
    rc, saved, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", result["batch_id"])
    )
    assert rc == 0 and [
        row["observation_status"] for row in saved[-1]["evidence"]["observations"]
    ] == ["SUCCESS", "FETCH_ERROR"], (saved, err)


def test_manual_batch_rejects_changed_supplement_and_stays_stale(tmp_path):
    root, config = settings(tmp_path, "manual")
    env = env_without_network(tmp_path, FAKE_BATCH)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    rc, initial, err = command(env, *run_args(root, config))
    assert rc == 10, (initial, err)
    run_id = next(row["run_id"] for row in initial if row.get("candidate_state") == "COMMITTED")
    assert confirm(env, root, initial[-1])["status"] == "COMMITTED"
    supplement = {
        "sources": [SOURCE],
        "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    path = root / "supplement.yaml"
    path.write_text(json.dumps(supplement))
    rc, rows, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-batch",
            "--scope-json",
            json.dumps(scope("SHFE")),
            "--scope-json",
            json.dumps(scope("DCE")),
        ),
    )
    assert rc == 10 and rows[-1]["status"] == "PENDING_REVIEW", (rows, err)
    pending = rows[-1]
    original = path.read_bytes()
    supplement["exceptions"] = [
        {
            "venue": "GFEX",
            "kind": "option",
            "product": "poption",
            "contract": "mock_gfex_option_contract",
            "trading_day": "2027-01-04",
            "action": "close_day",
            "window_id": None,
            "source_ids": [SOURCE["id"]],
        }
    ]
    path.write_text(json.dumps(supplement))
    baseline = (root / "CURRENT.json").read_bytes()
    args = operation(
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
    )
    rc, rejected, err = command(env, *args)
    assert rc != 0 and rejected[-1]["reason"] == "REVIEW_STALE", (rejected, err)
    assert (root / "CURRENT.json").read_bytes() == baseline
    path.write_bytes(original)
    rc, repeated, err = command(env, *args)
    assert rc != 0 and repeated[-1]["reason"] == "REVIEW_STALE", (repeated, err)


def _initialized_batch_root(tmp_path, mode, extra=""):
    root, config = settings(tmp_path, mode)
    env = env_without_network(tmp_path, FAKE_BATCH + extra)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    rc, initial, err = command(env, *run_args(root, config))
    assert rc == (0 if mode == "auto" else 10), (initial, err)
    run_id = next(row["run_id"] for row in initial if row.get("candidate_state") == "COMMITTED")
    if mode == "manual":
        assert confirm(env, root, initial[-1])["status"] == "COMMITTED"
    supplement = {
        "sources": [SOURCE],
        "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    (root / "supplement.yaml").write_text(json.dumps(supplement))
    return root, env, run_id


@pytest.mark.parametrize("case", ["overlap", "unmaterializable"])
def test_scope_preflight_rejects_before_allocation_or_fetch(tmp_path, case):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    first, second = scope("SHFE"), scope("DCE")
    if case == "overlap":
        second = scope("SHFE")
        second["target"]["valid_from"] = "2027-01-01"
    else:
        second["target"]["mock_contract"] = "mock_unknown_future_contract"
    before = (root / "CURRENT.json").read_bytes()
    rc, rows, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-batch",
            "--scope-json",
            json.dumps(first),
            "--scope-json",
            json.dumps(second),
        ),
    )
    assert rc != 0 and rows[-1]["reason"] == (
        "BATCH_SCOPE_OVERLAP" if case == "overlap" else "BATCH_TARGET_NOT_MATERIALIZABLE"
    ), (rows, err)
    assert not any(row.get("event") == "MOCK_BATCH_IDS_ALLOCATED" for row in rows)
    assert not (tmp_path / "calls").exists()
    assert (root / "CURRENT.json").read_bytes() == before


_DELETE_DURING_CAPTURE = """
_captured_observe = observation.observe
def removing_observe(**kwargs):
    result = _captured_observe(**kwargs)
    if os.environ.get('DELETE_BATCH_INTENT'):
        from pathlib import Path
        import subprocess, sys
        folder = Path(kwargs['root']) / 'observation-runs'
        path = next(p for p in folder.glob('mock-daily-batch-intent-*.json') if kwargs['observation_id'] in p.read_text())
        subprocess.run([sys.executable, '-c', 'import os,sys; os.unlink(sys.argv[1])', str(path)], check=True)
    return result
observation.observe = removing_observe
"""


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_intent_deleted_by_other_process_during_capture_is_orphaned(tmp_path, mode):
    root, env, run_id = _initialized_batch_root(tmp_path, mode, _DELETE_DURING_CAPTURE)
    env["DELETE_BATCH_INTENT"] = "1"
    before = (root / "CURRENT.json").read_bytes()
    rc, rows, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-batch",
            "--scope-json",
            json.dumps(scope("SHFE")),
            "--scope-json",
            json.dumps(scope("DCE")),
        ),
    )
    result = rows[-1]
    assert rc != 0 and result["reason"] == "BATCH_INTENT_RECONCILE_FIRST", (rows, err)
    assert result["publication"] == "NOT_ATTEMPTED"
    assert len((tmp_path / "calls").read_text().splitlines()) == 1
    assert (root / "CURRENT.json").read_bytes() == before
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", result["batch_id"])
    )
    assert (
        rc != 0
        and shown[-1]["status"] == "UNKNOWN"
        and shown[-1]["reason"] == "BATCH_ORPHAN_EVIDENCE"
    ), (shown, err)


def test_pending_confirmation_rejects_missing_intent(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "manual")
    before = (root / "CURRENT.json").read_bytes()
    rc, rows, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-batch",
            "--scope-json",
            json.dumps(scope("SHFE")),
            "--scope-json",
            json.dumps(scope("DCE")),
        ),
    )
    assert rc == 10 and rows[-1]["status"] == "PENDING_REVIEW", (rows, err)
    pending = rows[-1]
    (root / "observation-runs" / f"mock-daily-batch-intent-{pending['batch_id']}.json").unlink()
    args = operation(
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
    )
    rc, rejected, err = command(env, *args)
    assert rc != 0 and rejected[-1]["status"] != "COMMITTED", (rejected, err)
    assert (root / "CURRENT.json").read_bytes() == before
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", pending["batch_id"])
    )
    assert rc != 0 and shown[-1]["reason"] == "BATCH_ORPHAN_EVIDENCE", (shown, err)


_DELETE_BEFORE_STORE = """
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
_reserve_original = MockCalendarPublication._reserve
def removing_reserve(self, fresh, publication_id, version_id, *, state):
    if os.environ.get('DELETE_AT_RESERVE') and fresh.get('data_origin') == 'AKSHARE_MOCK':
        from pathlib import Path
        import subprocess, sys
        path = next((Path(self.root) / 'observation-runs').glob('mock-daily-batch-intent-*.json'))
        subprocess.run([sys.executable, '-c', 'import os,sys; os.unlink(sys.argv[1])', str(path)], check=True)
    return _reserve_original(self, fresh, publication_id, version_id, state=state)
MockCalendarPublication._reserve = removing_reserve
"""


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_store_lock_rechecks_intent_after_claim(tmp_path, mode):
    root, env, run_id = _initialized_batch_root(tmp_path, mode, _DELETE_BEFORE_STORE)
    before = (root / "CURRENT.json").read_bytes()
    if mode == "auto":
        env["DELETE_AT_RESERVE"] = "1"
    rc, rows, err = command(
        env,
        *operation(
            root,
            run_id,
            "mock-batch",
            "--scope-json",
            json.dumps(scope("SHFE")),
            "--scope-json",
            json.dumps(scope("DCE")),
        ),
    )
    result = rows[-1]
    if mode == "manual":
        assert rc == 10 and result["status"] == "PENDING_REVIEW", (rows, err)
        env["DELETE_AT_RESERVE"] = "1"
        rc, rows, err = command(
            env,
            *operation(
                root,
                result["run_id"],
                "mock-confirm",
                "--review-id",
                result["review_id"],
                "--review-sha256",
                result["review_payload_sha256"],
                "--reviewer",
                "MOCK_reviewer",
                "--confirmed-at",
                "2026-11-13T00:00:00.000000Z",
            ),
        )
        result = rows[-1]
    assert rc != 0 and result["status"] == "UNKNOWN", (rows, err)
    assert result["publication_visibility"] == "NOT_COMMITTED"
    assert result["reservation_state"] == "RESERVED"
    assert (root / "CURRENT.json").read_bytes() == before
