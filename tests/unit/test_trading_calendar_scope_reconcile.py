"""Original-ID scope ledger reconciliation using local simulation stores."""

import fcntl
import json

import pytest

from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation
from tests.unit.test_trading_calendar_scope_checks import (
    _FAIL_DIRECTORY_FSYNC,
    _FAIL_HEAD,
    batch_args,
)


def make_orphan(tmp_path, *, after_existing=False, extra=""):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto", _FAIL_HEAD + extra)
    if after_existing:
        rc, rows, err = command(env, *batch_args(root, run_id))
        assert rc == 0, (rows, err)
    before = (root / "CURRENT.json").read_bytes()
    env["SCOPE_HEAD_FAILURE"] = "BEFORE"
    rc, rows, err = command(env, *batch_args(root, run_id))
    assert rc != 0 and rows[-1]["reason"] == "SCOPE_CHECK_RECONCILE_FIRST", (rows, err)
    return root, env, run_id, rows[-1], before


def show_args(root, run_id, batch_id, *, reconcile=False):
    extra = ("--reconcile-scope-checks",) if reconcile else ()
    return operation(root, run_id, "mock-batch-show", "--batch-id", batch_id, *extra)


@pytest.mark.parametrize("after_existing", [False, True])
def test_unique_orphan_reconciles_by_original_id_without_publish(tmp_path, after_existing):
    root, env, run_id, result, before = make_orphan(tmp_path, after_existing=after_existing)
    batch_id = result["batch_id"]
    record = root / "observation-runs" / f"scope-check-batch-{batch_id}.json"
    original = record.read_bytes()
    calls = (tmp_path / "calls").read_bytes()
    rc, inspected, err = command(env, *show_args(root, run_id, batch_id))
    assert rc != 0 and inspected[-1]["scope_checks"]["reason"] == "ORPHAN_RECONCILABLE", (
        inspected,
        err,
    )
    del env["SCOPE_HEAD_FAILURE"]
    rc, recovered, err = command(env, *show_args(root, run_id, batch_id, reconcile=True))
    assert rc == 0 and recovered[-1]["scope_check_reconciliation"]["reason"] == "RECONCILED", (
        recovered,
        err,
    )
    assert recovered[-1]["scope_check_reconciliation"]["publication"] == "NOT_ATTEMPTED"
    assert record.read_bytes() == original and (tmp_path / "calls").read_bytes() == calls
    assert (root / "CURRENT.json").read_bytes() == before
    rc, repeated, err = command(env, *show_args(root, run_id, batch_id, reconcile=True))
    assert rc == 0 and repeated[-1]["scope_check_reconciliation"]["reason"] == "ALREADY_RECORDED", (
        repeated,
        err,
    )
    assert (root / "CURRENT.json").read_bytes() == before


@pytest.mark.parametrize("fault", ["PREDECESSOR", "CONTENT", "EXTRA"])
def test_orphan_with_unprovable_chain_is_not_reconciled(tmp_path, fault):
    from bt_api_py import trading_calendar_detail_collection as storage

    root, env, run_id, result, before = make_orphan(tmp_path)
    batch_id = result["batch_id"]
    record = root / "observation-runs" / f"scope-check-batch-{batch_id}.json"
    if fault == "PREDECESSOR":
        body = json.loads(record.read_bytes())["body"]
        body["previous_sha256"] = "a" * 64
        record.write_bytes(storage._envelope(body))
    elif fault == "CONTENT":
        oid = result["observations"][0]["observation_id"]
        (root / "akshare-observations" / f"result-{oid}.json").unlink()
    else:
        (record.parent / ("scope-check-batch-" + "f" * 32 + ".json")).write_bytes(
            record.read_bytes()
        )
    del env["SCOPE_HEAD_FAILURE"]
    rc, denied, err = command(env, *show_args(root, run_id, batch_id, reconcile=True))
    assert rc != 0 and denied[-1]["status"] == "UNKNOWN", (denied, err)
    assert not (record.parent / "scope-check-head.json").exists()
    assert (root / "CURRENT.json").read_bytes() == before


@pytest.mark.parametrize("fault", ["BEFORE", "AFTER", "FSYNC"])
def test_reconcile_write_uncertain_requires_original_id_readback(tmp_path, fault):
    root, env, run_id, result, before = make_orphan(tmp_path, extra=_FAIL_DIRECTORY_FSYNC)
    batch_id = result["batch_id"]
    if fault == "FSYNC":
        del env["SCOPE_HEAD_FAILURE"]
        env["FAIL_SCOPE_DIR_FSYNC"] = "1"
    else:
        env["SCOPE_HEAD_FAILURE"] = fault
    rc, attempt, err = command(env, *show_args(root, run_id, batch_id, reconcile=True))
    assert rc != 0 and attempt[-1]["status"] == "UNKNOWN", (attempt, err)
    assert attempt[-1]["scope_check_reconciliation"]["publication"] == "NOT_ATTEMPTED"
    assert (root / "CURRENT.json").read_bytes() == before
    env.pop("SCOPE_HEAD_FAILURE", None)
    env.pop("FAIL_SCOPE_DIR_FSYNC", None)
    rc, recovered, err = command(env, *show_args(root, run_id, batch_id, reconcile=True))
    expected = "RECONCILED" if fault == "BEFORE" else "ALREADY_RECORDED"
    assert rc == 0 and recovered[-1]["scope_check_reconciliation"]["reason"] == expected, (
        recovered,
        err,
    )
    assert (root / "CURRENT.json").read_bytes() == before


def test_reconcile_refuses_busy_store_lock(tmp_path):
    root, env, run_id, result, before = make_orphan(tmp_path)
    del env["SCOPE_HEAD_FAILURE"]
    with (root / ".lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rc, denied, err = command(env, *show_args(root, run_id, result["batch_id"], reconcile=True))
        assert rc != 0 and denied[-1]["status"] == "UNKNOWN", (denied, err)
    assert not (root / "observation-runs" / "scope-check-head.json").exists()
    assert (root / "CURRENT.json").read_bytes() == before


@pytest.mark.parametrize("change", ["CURRENT", "POLICY"])
def test_reconcile_never_publishes_after_baseline_or_policy_change(tmp_path, change):
    root, env, run_id, result, before = make_orphan(tmp_path)
    del env["SCOPE_HEAD_FAILURE"]
    if change == "CURRENT":
        path = root / "supplement.yaml"
        supplement = json.loads(path.read_text())
        supplement["exceptions"] = [
            {
                "venue": "GFEX",
                "kind": "option",
                "product": "poption",
                "contract": "mock_gfex_option_contract",
                "trading_day": "2027-01-04",
                "action": "close_day",
                "window_id": None,
                "source_ids": [supplement["sources"][0]["id"]],
            }
        ]
        path.write_text(json.dumps(supplement))
        rc, updated, err = command(env, *operation(root, run_id, "mock-update"))
        assert rc == 0 and updated[-1]["status"] == "COMMITTED", (updated, err)
        assert (root / "CURRENT.json").read_bytes() != before
    else:
        from tests.unit.test_trading_calendar_mock_policy_transition import begin, policy, set_mode

        rc, started, err = begin(env, root, run_id, "manual", "MOCK_reconcile_policy")
        assert rc == 10 and started[-1]["status"] == "PENDING_CONFIG", (started, err)
        set_mode(tmp_path / "config.json", root, "manual")
        rc, switched, err = command(
            env, *policy(root, run_id, "resume", "--operation-id", "MOCK_reconcile_policy")
        )
        assert rc == 0 and switched[-1]["policy"]["mode"] == "manual", (switched, err)
    current = (root / "CURRENT.json").read_bytes()
    publications = set((root / "publications").iterdir())
    rc, recovered, err = command(env, *show_args(root, run_id, result["batch_id"], reconcile=True))
    assert (
        rc == 0 and recovered[-1]["scope_check_reconciliation"]["publication"] == "NOT_ATTEMPTED"
    ), (recovered, err)
    assert (root / "CURRENT.json").read_bytes() == current
    assert set((root / "publications").iterdir()) == publications
