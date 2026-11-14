"""Offline end-to-end daily batch on the real simulation store."""

import json
from datetime import date

import pytest

from bt_api_py.trading_calendar_mock import SOURCE
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.unit.test_trading_calendar_bound_session_simulation import bind
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
def test_batch_updates_future_and_mock_option_exception_in_one_version(tmp_path, mode):
    root, env, original_run = _initialized_batch_root(tmp_path, mode)
    env["FAKE_AK_END"] = "10:30"
    path = root / "supplement.yaml"
    supplement = json.loads(path.read_text())
    option_exception = {
        "venue": "SHFE",
        "kind": "option",
        "product": "poption",
        "contract": "mock_shfe_option_contract",
        "trading_day": "2027-01-04",
        "action": "cancel_window",
        "window_id": "day",
        "source_ids": [SOURCE["id"]],
    }
    supplement["exceptions"] = [option_exception]
    path.write_text(json.dumps(supplement))
    baseline = (root / "CURRENT.json").read_bytes()
    before_publications = set((root / "publications").iterdir())
    rc, rows, err = command(
        env,
        *operation(
            root,
            original_run,
            "mock-batch",
            "--scope-json",
            json.dumps(scope("SHFE")),
            "--scope-json",
            json.dumps(scope("DCE")),
        ),
    )
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    result = rows[-1]
    assert result["status"] == ("COMMITTED" if mode == "auto" else "PENDING_REVIEW")
    assert all(row["target"]["kind"] == "future" for row in result["observations"])
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    if mode == "manual":
        assert (root / "CURRENT.json").read_bytes() == baseline
        pending_diff = result["proposal"]["difference"]["changes"]
        assert any(row["operation"] == "added" for row in pending_diff["exceptions"])
        assert pending_diff["trading_hours"]
        assert confirm(env, root, result)["status"] == "COMMITTED"
    assert len(set((root / "publications").iterdir()) - before_publications) == 1
    store = TradingCalendarStore(root)
    current = store.read_current()
    assert option_exception in current.content["exceptions"]
    difference = store.read_publication_policy(current.publication_id)["difference"]["changes"]
    assert any(row["operation"] == "added" for row in difference["exceptions"])
    assert difference["trading_hours"]
    view = bind(store).capture_read_view(mode="SIMULATION_ONLY")
    future = view.query(
        venue="SHFE", kind="future", product="pfuture", contract="mock_shfe_future_contract",
        trading_day=date(2027, 1, 4),
    )
    option = view.query(
        venue="SHFE", kind="option", product="poption", contract="mock_shfe_option_contract",
        trading_day=date(2027, 1, 4),
    )
    assert future.result.status == option.result.status == "COMPLETE"
    assert future.result.calendar_ref == option.result.calendar_ref == current.version_id
    assert future.simulation_provenance["target"]["kind"] == "future"
    assert any(row["source_type"] == "AKSHARE" for row in future.simulation_provenance["windows"])
    assert any((window.end_local.hour, window.end_local.minute) == (10, 30) for window in future.result.windows)
    assert option.simulation_provenance is None and option.declared_mock_provenance
    assert all(window.window_id != "day" for window in option.result.windows)
    assert any(window.window_id == "night" for window in option.result.windows)
    assert all(
        row["source_type"] == "MOCK" and row["source_authentication"] == "NOT_PROVEN"
        for row in option.declared_mock_provenance["windows"]
    )
    day = view.query_venue_day(mode="SIMULATION_ONLY", venue="SHFE", trading_day=date(2027, 1, 4))
    assert day.binding.calendar_ref == current.version_id and day.source_authentication == "NOT_PROVEN"
    outside = view.query(
        venue="SHFE", kind="option", product="poption", contract="mock_shfe_option_contract",
        trading_day=date(2027, 1, 5),
    )
    assert outside.result.status == "UNKNOWN"


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


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_one_failed_scope_uses_mock_fallback(tmp_path, mode, monkeypatch):
    root, config = settings(tmp_path, mode)
    env = env_without_network(tmp_path, FAKE_BATCH)
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
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    assert result["batch_status"] == "PARTIAL"
    assert result["scope_checks"]["status"] == "VISIBLE_UNAUTHENTICATED"
    check_by_venue = {
        row["target"]["venue"]: row["last_success"] for row in result["scope_checks"]["scopes"]
    }
    assert check_by_venue["SHFE"]["status"] == "VISIBLE_UNAUTHENTICATED"
    assert check_by_venue["SHFE"]["clock"] == "CLOCK_UNVERIFIED"
    assert check_by_venue["DCE"]["status"] == "UNKNOWN"
    assert result["scope_checks"]["venue_last_success"] == "UNKNOWN"
    assert [row["observation_status"] for row in result["observations"]] == [
        "SUCCESS",
        "FETCH_ERROR",
    ]
    assert all(
        row["source_type"] == "AKSHARE" and row["source_authentication"] == "NOT_PROVEN"
        for row in result["observations"]
    )
    assert [row["disposition"] for row in result["observations"]] == [
        "UPDATED_AKSHARE",
        "MOCK_FALLBACK",
    ]
    if mode == "manual":
        assert (root / "CURRENT.json").read_bytes() == current
        assert confirm(env, root, result)["status"] == "COMMITTED"
    assert (root / "CURRENT.json").read_bytes() != current
    version = TradingCalendarStore(root).read_current()
    assert version.simulation_provenance["schema"] == "bt_api.calendar-mixed-scopes-provenance/3"
    assert len(version.simulation_provenance["scopes"]) == 1
    assert (
        json.loads((root / "versions" / f"{version.version_id}.json").read_bytes())["schema"]
        == "bt_api.calendar-store.snapshot/6"
    )
    operator = MockCalendarPublication(root=str(root), run_id=run_id)
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
        assert (query.simulation_provenance is not None) == (venue == "SHFE")
    rc, saved, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", result["batch_id"])
    )
    assert rc == 0 and [
        row["observation_status"] for row in saved[-1]["evidence"]["observations"]
    ] == ["SUCCESS", "FETCH_ERROR"], (saved, err)
    assert saved[-1]["scope_checks"]["scopes"] == result["scope_checks"]["scopes"]
    current = (root / "CURRENT.json").read_bytes()
    rc, repeated, err = command(env, *args)
    assert rc == 10 and repeated[-1]["status"] == "SAME", (repeated, err)
    assert repeated[-1]["batch_status"] == "PARTIAL"
    assert (root / "CURRENT.json").read_bytes() == current
    # Model an older reader that does not recognize snapshot /6.
    from bt_api_py import trading_calendar_store as store_module

    monkeypatch.setattr(store_module, "_SNAPSHOT_PARTIAL", "unrecognized")
    with pytest.raises(store_module.CalendarStoreError):
        TradingCalendarStore(root).read_current()


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_partial_batch_retains_previous_scope(tmp_path, mode):
    root, config = settings(tmp_path, mode)
    env = env_without_network(tmp_path, FAKE_BATCH)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    rc, initial, err = command(env, *run_args(root, config))
    assert rc == (0 if mode == "auto" else 10), (initial, err)
    run_id = next(row["run_id"] for row in initial if row.get("candidate_state") == "COMMITTED")
    if mode == "manual":
        assert confirm(env, root, initial[-1])["status"] == "COMMITTED"
    (root / "supplement.yaml").write_text(
        json.dumps(
            {
                "sources": [SOURCE],
                "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
                "trading_hours": [],
                "exceptions": [],
            }
        )
    )
    args = operation(
        root,
        run_id,
        "mock-batch",
        "--scope-json",
        json.dumps(scope("SHFE")),
        "--scope-json",
        json.dumps(scope("DCE")),
    )
    rc, first, err = command(env, *args)
    assert rc == (0 if mode == "auto" else 10), (first, err)
    initial_checks = {
        row["target"]["venue"]: row["last_success"] for row in first[-1]["scope_checks"]["scopes"]
    }
    if mode == "manual":
        assert confirm(env, root, first[-1])["status"] == "COMMITTED"
    store = TradingCalendarStore(root)
    before = store.read_current()
    old = next(s for s in before.simulation_provenance["scopes"] if s["target"]["venue"] == "DCE")
    dce_row = next(
        row
        for row in before.content["trading_hours"]
        if row["venue"] == "DCE" and row["kind"] == "future"
    )
    env["FAIL_DCE"] = "1"
    env["FAKE_AK_END"] = "10:30"
    rc, rows, err = command(env, *args)
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    result = rows[-1]
    assert [row["disposition"] for row in result["observations"]] == [
        "UPDATED_AKSHARE",
        "RETAINED_AKSHARE",
    ]
    latest_checks = {
        row["target"]["venue"]: row["last_success"] for row in result["scope_checks"]["scopes"]
    }
    assert latest_checks["DCE"] == initial_checks["DCE"]
    assert latest_checks["SHFE"]["sequence"] > initial_checks["SHFE"]["sequence"]
    if mode == "manual":
        assert store.read_current().version_id == before.version_id
        assert confirm(env, root, result)["status"] == "COMMITTED"
    after = store.read_current()
    assert after.version_id != before.version_id
    assert (
        next(s for s in after.simulation_provenance["scopes"] if s["target"]["venue"] == "DCE")
        == old
    )
    assert (
        next(
            row
            for row in after.content["trading_hours"]
            if row["venue"] == "DCE" and row["kind"] == "future"
        )
        == dce_row
    )


def test_all_failed_is_recorded_without_publication(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, FAKE_BATCH)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    rc, initial, err = command(env, *run_args(root, config))
    assert rc == 0, (initial, err)
    run_id = next(row["run_id"] for row in initial if row.get("candidate_state") == "COMMITTED")
    (root / "supplement.yaml").write_text(
        json.dumps(
            {
                "sources": [SOURCE],
                "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
                "trading_hours": [],
                "exceptions": [],
            }
        )
    )
    before = (root / "CURRENT.json").read_bytes()
    env["FAKE_AK_STATUS"] = "ERROR"
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
    assert rc != 0 and rows[-1]["reason"] == "BATCH_ALL_FAILED", (rows, err)
    assert all(row["check_status"] == "FAILED" for row in rows[-1]["observations"])
    assert (root / "CURRENT.json").read_bytes() == before
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", rows[-1]["batch_id"])
    )
    assert rc == 0 and len(shown[-1]["evidence"]["observations"]) == 2, (shown, err)


@pytest.mark.parametrize("partial", [False, True])
def test_manual_batch_rejects_changed_supplement_and_stays_stale(tmp_path, partial):
    root, config = settings(tmp_path, "manual")
    env = env_without_network(tmp_path, FAKE_BATCH)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    if partial:
        env["FAIL_DCE"] = "1"
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
    if partial:
        assert pending["batch_status"] == "PARTIAL"
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


_UNKNOWN_FAILURES = """
_original_collect = collect_akshare_contract
def fault_collect(**options):
    if options['venue'] == 'DCE' and os.environ.get('DCE_FAULT'):
        if os.environ['DCE_FAULT'] == 'OMITTED':
            rows = [['交易时间', '日盘 09:00-10:00']] + [[f'field{i}', 'x' * 3600] for i in range(500)]
            options['_runner'] = lambda *_: ('CHILD_OK', {'status': 'OK', 'rows': rows})
        else:
            options['_runner'] = lambda *_: ('CHILD_CLEANUP_UNCERTAIN', None)
    return _original_collect(**options)
collect_akshare_contract = fault_collect
"""


@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("fault", ["OMITTED", "CLEANUP"])
def test_unknown_failed_observation_never_publishes(tmp_path, mode, fault):
    root, env, run_id = _initialized_batch_root(tmp_path, mode, _UNKNOWN_FAILURES)
    env["DCE_FAULT"] = fault
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
    assert rc != 0 and rows[-1]["status"] == "UNKNOWN", (rows, err)
    result = rows[-1]
    failed = result["observations"][1]
    assert failed["observation_status"] == "UNKNOWN"
    assert failed["check_status"] == "UNKNOWN" and failed["disposition"] == "UNAVAILABLE"
    if fault == "OMITTED":
        assert failed["observation_diagnostics"]["data_state"] == "DATA_OMITTED"
    else:
        assert failed["observation_diagnostics"]["transport_status"] == "CHILD_CLEANUP_UNCERTAIN"
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    assert (root / "CURRENT.json").read_bytes() == before


_BREAK_RESULT_DURING_CAPTURE = """
_original_observe_for_failure = observation.observe
def break_failure_result(**kwargs):
    result = _original_observe_for_failure(**kwargs)
    if kwargs['venue'] == 'DCE' and os.environ.get('BREAK_FAILED_RESULT'):
        from pathlib import Path
        path = Path(kwargs['root']) / 'akshare-observations' / ('result-' + kwargs['observation_id'] + '.json')
        if os.environ['BREAK_FAILED_RESULT'] == 'DELETE':
            path.unlink()
        else:
            path.write_text('{}')
    return result
observation.observe = break_failure_result
"""


@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("action", ["DELETE", "ALTER"])
def test_failure_result_changed_during_capture_is_unknown(tmp_path, mode, action):
    root, env, run_id = _initialized_batch_root(tmp_path, mode, _BREAK_RESULT_DURING_CAPTURE)
    env.update(FAIL_DCE="1", BREAK_FAILED_RESULT=action)
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
    assert rc != 0 and rows[-1]["status"] == "UNKNOWN", (rows, err)
    assert rows[-1]["observations"][1]["reason"] == "FAILURE_OBSERVATION_RECONCILE_FIRST"
    assert (root / "CURRENT.json").read_bytes() == before


@pytest.mark.parametrize("action", ["DELETE", "ALTER"])
def test_pending_partial_confirmation_requires_original_failure_result(tmp_path, action):
    root, env, run_id = _initialized_batch_root(tmp_path, "manual")
    env["FAIL_DCE"] = "1"
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
    oid = pending["observations"][1]["observation_id"]
    path = root / "akshare-observations" / f"result-{oid}.json"
    if action == "DELETE":
        path.unlink()
    else:
        from bt_api_py import trading_calendar_detail_collection as storage

        altered = json.loads(path.read_bytes())["body"]
        altered["captured_at"] = "2020-01-01T00:00:00+00:00"
        path.write_bytes(storage._envelope(altered))
    rc, rejected, err = command(
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
    assert rc != 0 and rejected[-1]["status"] == "UNKNOWN", (rejected, err)
    assert (root / "CURRENT.json").read_bytes() == before
    rc, shown, err = command(
        env, *operation(root, run_id, "mock-batch-show", "--batch-id", pending["batch_id"])
    )
    assert rc != 0 and shown[-1]["status"] == "UNKNOWN", (shown, err)


def test_partial_history_requires_original_failure_result(tmp_path):
    root, env, run_id = _initialized_batch_root(tmp_path, "auto")
    env["FAIL_DCE"] = "1"
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
    assert rc == 0 and rows[-1]["status"] == "COMMITTED", (rows, err)
    result = rows[-1]
    show_args = operation(
        root,
        result["run_id"],
        "mock-show",
        "--candidate-id",
        result["candidate_id"],
        "--publication-id",
        result["publication_id"],
    )
    rc, shown, err = command(env, *show_args)
    assert shown[-1]["candidate_history"]["status"] == "VISIBLE_UNAUTHENTICATED", (shown, err)
    oid = result["observations"][1]["observation_id"]
    (root / "akshare-observations" / f"result-{oid}.json").unlink()
    rc, shown, err = command(env, *show_args)
    assert shown[-1]["candidate_history"]["status"] == "UNKNOWN", (shown, err)


_DELETE_FAILURE_BEFORE_STORE = """
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
_reserve_before_failure = MockCalendarPublication._reserve
def remove_failed_result(self, fresh, publication_id, version_id, *, state):
    if os.environ.get('DELETE_FAILURE_AT_RESERVE') and fresh.get('data_origin') == 'AKSHARE_MOCK':
        from pathlib import Path
        folder = Path(self.root) / 'akshare-observations'
        intent = next(path for path in folder.glob('intent-*.json') if 'DCE' in path.read_text())
        (folder / intent.name.replace('intent-', 'result-', 1)).unlink()
    return _reserve_before_failure(self, fresh, publication_id, version_id, state=state)
MockCalendarPublication._reserve = remove_failed_result
"""


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_store_lock_rechecks_failed_result(tmp_path, mode):
    root, env, run_id = _initialized_batch_root(tmp_path, mode, _DELETE_FAILURE_BEFORE_STORE)
    env["FAIL_DCE"] = "1"
    before = (root / "CURRENT.json").read_bytes()
    if mode == "auto":
        env["DELETE_FAILURE_AT_RESERVE"] = "1"
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
        env["DELETE_FAILURE_AT_RESERVE"] = "1"
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
    assert (root / "CURRENT.json").read_bytes() == before


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
