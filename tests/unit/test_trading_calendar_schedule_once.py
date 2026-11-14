"""Offline local scheduling contract; all collectors are synthetic."""

import json
import subprocess
import sys
import threading
from datetime import UTC, datetime

import pytest

from bt_api_py import trading_calendar_schedule_once as schedule
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root, scope
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation

INSTANT = datetime(2026, 11, 13, 16, 30, tzinfo=UTC)


def setup_root(tmp_path, mode="auto", extra=""):
    root, env, original = _initialized_batch_root(tmp_path, mode, extra)
    config = {
        "schema": schedule._CONFIG,
        "environment": "SIMULATION_ONLY",
        "root": str(root),
        "job_key": "daily-ctp",
        "original_run_id": original,
        "scopes": [scope("DCE"), scope("SHFE")],
        "supplement": str(root / "supplement.yaml"),
        "budget_seconds": 60,
    }
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    return root, env, config, path


def call(path):
    return schedule.run_once(config_file=str(path), filesystem=schedule.FS)


def declared_result(kwargs, status):
    ids = kwargs["identity_bundle"]
    return {
        "status": status,
        "identity_bundle": ids,
        "requested_date": kwargs["requested_date"].isoformat(),
        "batch_status": "COMPLETE",
        "batch_evidence_sha256": "f" * 64,
        "review_payload_sha256": "e" * 64,
        **{
            key: ids[key]
            for key in (
                "original_run_id",
                "run_id",
                "batch_id",
                "capture_id",
                "parent_id",
                "derived_id",
                "candidate_id",
                "review_id",
                "publication_id",
                "version_id",
            )
        },
    }


def test_once_pending_blocks_cross_midnight_and_batch_runs_outside_store_lock(
    tmp_path, monkeypatch
):
    root, _, _, path = setup_root(tmp_path)
    clock_calls = []

    def clock():
        clock_calls.append(1)
        return INSTANT

    monkeypatch.setattr(schedule, "_now_utc", clock)
    calls = []

    def fake_batch(**kwargs):
        with TradingCalendarStore(root)._writer_lock():
            pass
        calls.append(kwargs)
        return {
            "status": "UNKNOWN",
            "identity_bundle": kwargs["identity_bundle"],
            "requested_date": kwargs["requested_date"].isoformat(),
        }

    monkeypatch.setattr(schedule.batch, "run", fake_batch)
    first = call(path)
    assert first["status"] == "UNKNOWN" and len(calls) == 1 and clock_calls == [1]
    assert first["requested_date"] == "2026-11-14"
    assert calls[0]["requested_date"].isoformat() == "2026-11-14"
    monkeypatch.setattr(schedule, "_now_utc", lambda: pytest.fail("pending reread clock"))
    repeated = call(path)
    assert repeated["status"] == "UNKNOWN"
    assert repeated["operation_id"] == first["operation_id"]
    assert repeated["identity_bundle"] == first["identity_bundle"] and len(calls) == 1
    assert (
        schedule.inspect(
            root=str(root), operation_id=first["operation_id"], filesystem=schedule.FS
        )["status"]
        == "UNKNOWN"
    )


def test_two_concurrent_starts_call_batch_once(tmp_path, monkeypatch):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    entered, release = threading.Event(), threading.Event()
    calls, output = [], []

    def fake_batch(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(8)
        return {
            "status": "UNKNOWN",
            "identity_bundle": kwargs["identity_bundle"],
            "requested_date": kwargs["requested_date"].isoformat(),
        }

    monkeypatch.setattr(schedule.batch, "run", fake_batch)
    thread = threading.Thread(target=lambda: output.append(call(path)))
    thread.start()
    try:
        assert entered.wait(8)
        second = call(path)
        assert second["status"] == "UNKNOWN" and second.get("operation_id")
    finally:
        release.set()
        thread.join(8)
    assert len(calls) == 1 and output[0]["operation_id"] == second["operation_id"]
    assert (root / "observation-runs" / schedule._PENDING_FILE).exists()


@pytest.mark.parametrize(
    "stage", ["terminal_before", "terminal_after", "index_before", "index_after"]
)
def test_partial_terminal_stages_never_restart_batch(tmp_path, monkeypatch, stage):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    calls = []

    def fake_batch(**kwargs):
        calls.append(1)
        return declared_result(kwargs, "COMMITTED")

    monkeypatch.setattr(schedule.batch, "run", fake_batch)
    monkeypatch.setattr(
        schedule,
        "_batch_proof",
        lambda *args, **kwargs: {
            "kind": "COMMITTED",
            "evidence_sha256": "f" * 64,
            "candidate_body_sha256": "a" * 64,
            "candidate_payload_sha256": "b" * 64,
            "pointer_sha256": "c" * 64,
            "review_payload_sha256": None,
        },
    )
    monkeypatch.setattr(schedule, "_verify_locked", lambda *args: None)
    original = schedule._write

    def fault(fd, chain, name, value):
        target = (
            name.startswith("schedule-once-terminal-")
            and not name.startswith("schedule-once-terminal-attempt-")
            if stage.startswith("terminal")
            else name.startswith("schedule-once-date-")
        )
        if target and stage.endswith("before"):
            raise OSError("before stage target")
        digest = original(fd, chain, name, value)
        if target and stage.endswith("after"):
            raise OSError("after stage target")
        return digest

    monkeypatch.setattr(schedule, "_write", fault)
    first = call(path)
    assert first["status"] == "UNKNOWN" and calls == [1]
    assert (root / "observation-runs" / schedule._PENDING_FILE).exists()
    monkeypatch.setattr(schedule, "_write", original)
    assert call(path)["operation_id"] == first["operation_id"] and calls == [1]
    recovered = schedule.reconcile(
        root=str(root), operation_id=first["operation_id"], filesystem=schedule.FS
    )
    assert recovered["status"] == ("COMMITTED" if stage.endswith("after") else "UNKNOWN")
    assert calls == [1]


def test_unknown_pending_unlink_cannot_repeat_same_day(tmp_path, monkeypatch):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    calls = []

    def fake_batch(**kwargs):
        calls.append(1)
        return declared_result(kwargs, "COMMITTED")

    monkeypatch.setattr(schedule.batch, "run", fake_batch)
    monkeypatch.setattr(
        schedule,
        "_batch_proof",
        lambda *args, **kwargs: {
            "kind": "COMMITTED",
            "evidence_sha256": "f" * 64,
            "candidate_body_sha256": "a" * 64,
            "candidate_payload_sha256": "b" * 64,
            "pointer_sha256": "c" * 64,
            "review_payload_sha256": None,
        },
    )
    monkeypatch.setattr(schedule, "_verify_locked", lambda *args: None)
    original = schedule._release_pending

    def uncertain(*args):
        original(*args)
        raise OSError("uncertain unlink")

    monkeypatch.setattr(schedule, "_release_pending", uncertain)
    first = call(path)
    assert first["status"] == "UNKNOWN" and calls == [1]
    monkeypatch.setattr(schedule, "_release_pending", original)
    again = call(path)
    assert again["status"] == "ALREADY_COMPLETED" and again["operation_id"] == first["operation_id"]
    assert calls == [1]
    shown = schedule.inspect(
        root=str(root), operation_id=first["operation_id"], filesystem=schedule.FS
    )
    assert shown["status"] == "LOCAL_HISTORICAL_RECORD_ONLY"
    assert shown["release"] == "NOT_PROVEN_BY_READBACK"


@pytest.mark.parametrize("stage", ["pending", "intent", "returned"])
def test_uncertain_durable_write_never_restarts_batch(tmp_path, monkeypatch, stage):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    calls = []

    def fake_batch(**kwargs):
        calls.append(1)
        return {
            "status": "UNKNOWN",
            "identity_bundle": kwargs["identity_bundle"],
            "requested_date": kwargs["requested_date"].isoformat(),
        }

    monkeypatch.setattr(schedule.batch, "run", fake_batch)
    original = schedule._write

    def uncertain(fd, chain, name, value):
        digest = original(fd, chain, name, value)
        selected = (
            name == schedule._PENDING_FILE
            if stage == "pending"
            else name.startswith("schedule-once-" + stage + "-")
        )
        if selected:
            raise OSError("unknown after durable write")
        return digest

    monkeypatch.setattr(schedule, "_write", uncertain)
    first = call(path)
    assert first["status"] == "UNKNOWN" and first.get("operation_id")
    assert len(calls) == (1 if stage == "returned" else 0)
    monkeypatch.setattr(schedule, "_write", original)
    again = call(path)
    assert again["operation_id"] == first["operation_id"]
    assert len(calls) == (1 if stage == "returned" else 0)
    assert (root / "observation-runs" / schedule._PENDING_FILE).exists()


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_interrupt_after_pending_preserves_original_ids(tmp_path, monkeypatch, signal):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    calls = []
    monkeypatch.setattr(schedule.batch, "run", lambda **kwargs: calls.append(kwargs))
    original = schedule._write

    def interrupt(fd, chain, name, value):
        digest = original(fd, chain, name, value)
        if name == schedule._PENDING_FILE:
            raise signal()
        return digest

    monkeypatch.setattr(schedule, "_write", interrupt)
    with pytest.raises(signal) as caught:
        call(path)
    assert caught.value.operation_id and caught.value.identity_bundle
    monkeypatch.setattr(schedule, "_write", original)
    repeated = call(path)
    assert repeated["operation_id"] == caught.value.operation_id
    assert repeated["identity_bundle"] == caught.value.identity_bundle
    assert not calls
    assert (root / "observation-runs" / schedule._PENDING_FILE).exists()


@pytest.mark.parametrize(
    "fault", ["link", "parent_link", "hardlink", "duplicate", "unknown_field", "change"]
)
def test_unsafe_or_changed_config_does_not_invoke_batch(tmp_path, monkeypatch, fault):
    _, _, config, path = setup_root(tmp_path)
    calls = []
    monkeypatch.setattr(schedule.batch, "run", lambda **kwargs: calls.append(kwargs))
    if fault == "link":
        link = tmp_path / "linked.json"
        link.symlink_to(path)
        path = link
    elif fault == "parent_link":
        link = tmp_path / "linked-dir"
        link.symlink_to(tmp_path, target_is_directory=True)
        path = link / path.name
    elif fault == "hardlink":
        import os

        os.link(path, tmp_path / "hardlink.json")
    elif fault == "duplicate":
        path.write_text('{"schema":"x","schema":"y"}')
    elif fault == "unknown_field":
        config["credential"] = "not-allowed"
        path.write_text(json.dumps(config))
    else:
        original = schedule._read_config
        counter = [0]

        def changed(*args):
            result = original(*args)
            counter[0] += 1
            if counter[0] == 1:
                changed_config = dict(config, job_key="other")
                path.write_text(json.dumps(changed_config))
            return result

        monkeypatch.setattr(schedule, "_read_config", changed)
    assert call(path)["status"] == "UNKNOWN"
    assert not calls


@pytest.mark.parametrize("status", ["COMMITTED", "SAME", "PENDING_REVIEW"])
def test_status_without_original_evidence_never_terminalizes(tmp_path, monkeypatch, status):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    calls = []

    def fake_batch(**kwargs):
        calls.append(1)
        return declared_result(kwargs, status)

    monkeypatch.setattr(schedule.batch, "run", fake_batch)
    first = call(path)
    assert first["status"] == "UNKNOWN" and calls == [1]
    assert (root / "observation-runs" / schedule._PENDING_FILE).exists()
    assert not list((root / "observation-runs").glob("schedule-once-terminal-*.json"))
    assert call(path)["operation_id"] == first["operation_id"] and calls == [1]


def test_mismatched_batch_result_identity_never_terminalizes(tmp_path, monkeypatch):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    calls = []

    def wrong_batch(**kwargs):
        calls.append(1)
        return {**declared_result(kwargs, "COMMITTED"), "publication_id": "MOCK_wrong"}

    monkeypatch.setattr(schedule.batch, "run", wrong_batch)
    first = call(path)
    assert first["status"] == "UNKNOWN" and calls == [1]
    assert (root / "observation-runs" / schedule._PENDING_FILE).exists()
    assert not list((root / "observation-runs").glob("schedule-once-terminal-*.json"))


@pytest.mark.parametrize("barrier", ["migration", "store_pending", "lock_symlink"])
def test_existing_store_barrier_fails_before_schedule_pending(tmp_path, monkeypatch, barrier):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    calls = []
    monkeypatch.setattr(schedule.batch, "run", lambda **kwargs: calls.append(kwargs))
    if barrier == "migration":
        (root / "observation-runs" / "scope-check-migration-pending.json").write_text("{}")
    elif barrier == "store_pending":
        (root / "PENDING.json").write_text("{}")
    else:
        lock = root / ".lock"
        lock.rename(root / ".lock-saved")
        lock.symlink_to(root / ".lock-saved")
    assert call(path)["status"] == "UNKNOWN"
    assert not calls and not (root / "observation-runs" / schedule._PENDING_FILE).exists()


@pytest.mark.parametrize("fault", ["naive_clock", "scope_order", "scope_timeout"])
def test_invalid_clock_or_scope_never_creates_schedule_pending(tmp_path, monkeypatch, fault):
    root, _, config, path = setup_root(tmp_path)
    calls = []
    monkeypatch.setattr(schedule.batch, "run", lambda **kwargs: calls.append(kwargs))
    if fault == "naive_clock":
        monkeypatch.setattr(schedule, "_now_utc", lambda: datetime(2026, 11, 13, 16, 30))
    else:
        monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
        if fault == "scope_order":
            config["scopes"].reverse()
        else:
            config["scopes"][0]["timeout"] = -1
        path.write_text(json.dumps(config))
    assert call(path)["status"] == "UNKNOWN"
    assert not calls and not (root / "observation-runs" / schedule._PENDING_FILE).exists()


def _schedule_subprocess(env, path, date_utc="2026-11-13T16:30:00+00:00", code="run"):
    script = r"""
import json,sys
from datetime import datetime
from bt_api_py import trading_calendar_schedule_once as s
s._now_utc=lambda: datetime.fromisoformat(sys.argv[2])
if sys.argv[3] == 'run': value=s.run_once(config_file=sys.argv[1],filesystem=s.FS)
elif sys.argv[3] == 'reconcile': value=s.reconcile(root=sys.argv[4],operation_id=sys.argv[5],filesystem=s.FS)
else: value=s.inspect(root=sys.argv[4],operation_id=sys.argv[5],filesystem=s.FS)
print(json.dumps({'final':value},ensure_ascii=True))
"""
    args = [sys.executable, "-c", script, str(path), date_utc, code]
    return args


def execute(env, args):
    process = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout.splitlines()[-1])["final"]


@pytest.mark.parametrize("selection", ["default", "explicit"])
def test_supplement_default_or_explicit_with_original_config_identity(tmp_path, selection):
    root, env, config, path = setup_root(tmp_path)
    default = root / "supplement.yaml"
    if selection == "default":
        del config["supplement"]
    else:
        override = tmp_path / "other-supplement.yaml"
        default.rename(override)
        config["supplement"] = str(override)
    path.write_text(json.dumps(config))
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED", first
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    intent_path = root / "observation-runs" / schedule._name("intent", first["operation_id"])
    pending, _ = schedule.storage._unwrap(intent_path.read_bytes())
    assert pending["config"] == config
    assert pending["config_sha256"] == schedule._digest(config)
    assert schedule._validate_pending(pending, str(root)) == pending
    repeated = execute(env, _schedule_subprocess(env, path))
    assert repeated["status"] == "ALREADY_COMPLETED"
    assert repeated["operation_id"] == first["operation_id"]
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


@pytest.mark.parametrize("value", [None, "", "relative.yaml", "/tmp/../supplement.yaml"])
def test_explicit_invalid_supplement_does_not_claim_or_call_batch(tmp_path, monkeypatch, value):
    root, _, config, path = setup_root(tmp_path)
    config["supplement"] = value
    path.write_text(json.dumps(config))
    calls = []
    monkeypatch.setattr(schedule.batch, "run", lambda **kwargs: calls.append(kwargs))
    assert call(path)["status"] == "UNKNOWN"
    assert not calls
    assert not (root / "observation-runs" / schedule._PENDING_FILE).exists()


@pytest.mark.parametrize("fault", ["missing", "invalid"])
def test_default_supplement_failure_keeps_original_operation(tmp_path, fault):
    root, env, config, path = setup_root(tmp_path)
    del config["supplement"]
    path.write_text(json.dumps(config))
    supplement = root / "supplement.yaml"
    if fault == "missing":
        supplement.unlink()
    else:
        supplement.write_text("not valid: [")
    current = (root / "CURRENT.json").read_bytes()
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "UNKNOWN" and first.get("operation_id"), first
    calls_path = tmp_path / "calls"
    calls = calls_path.read_text() if calls_path.exists() else ""
    second = execute(env, _schedule_subprocess(env, path))
    assert second["status"] == "UNKNOWN"
    assert second["operation_id"] == first["operation_id"]
    assert (calls_path.read_text() if calls_path.exists() else "") == calls
    assert (root / "CURRENT.json").read_bytes() == current


def test_default_supplement_changed_after_review_cannot_publish(tmp_path):
    root, env, config, path = setup_root(tmp_path, mode="manual")
    del config["supplement"]
    path.write_text(json.dumps(config))
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW", first
    current = (root / "CURRENT.json").read_bytes()
    supplement_path = root / "supplement.yaml"
    supplement = json.loads(supplement_path.read_text())
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
    supplement_path.write_text(json.dumps(supplement))
    shown = execute(
        env, _schedule_subprocess(env, path, code="inspect") + [str(root), first["operation_id"]]
    )
    assert shown["status"] == "PENDING_REVIEW" and shown["operation_id"] == first["operation_id"]
    ids = first["identity_bundle"]
    rc, rows, err = command(
        env,
        *operation(
            root,
            ids["run_id"],
            "mock-confirm",
            "--review-id",
            ids["review_id"],
            "--review-sha256",
            first["review_payload_sha256"],
            "--reviewer",
            "MOCK_reviewer",
            "--confirmed-at",
            "2026-11-13T00:00:00.000000Z",
        ),
    )
    assert rc != 0 and rows[-1]["reason"] == "REVIEW_STALE", (rows, err)
    repeat = execute(env, _schedule_subprocess(env, path))
    assert repeat["status"] == "UNKNOWN" and repeat["operation_id"] == first["operation_id"]
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    assert (root / "CURRENT.json").read_bytes() == current


def test_real_local_mock_publication_and_same_day_fence(tmp_path):
    root, env, _, path = setup_root(tmp_path)
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED", first
    assert first["requested_date"] == "2026-11-14"
    assert not (root / "observation-runs" / schedule._PENDING_FILE).exists()
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    second = execute(env, _schedule_subprocess(env, path))
    assert (
        second["status"] == "ALREADY_COMPLETED" and second["operation_id"] == first["operation_id"]
    )
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    shown = execute(
        env, _schedule_subprocess(env, path, code="inspect") + [str(root), first["operation_id"]]
    )
    assert shown["status"] == "LOCAL_HISTORICAL_RECORD_ONLY"
    assert shown["release"] == "NOT_PROVEN_BY_READBACK"
    folder = root / "observation-runs"
    for kind in ("terminal", "intent", "returned", "terminal-attempt", "index-attempt"):
        original = folder / schedule._name(kind, first["operation_id"])
        saved = folder / (original.name + ".saved")
        original.rename(saved)
        try:
            assert execute(env, _schedule_subprocess(env, path))["status"] == "UNKNOWN"
            assert len((tmp_path / "calls").read_text().splitlines()) == 2
        finally:
            saved.rename(original)
    next_day = execute(env, _schedule_subprocess(env, path, date_utc="2026-11-15T00:00:00+00:00"))
    assert next_day["status"] == "SAME", next_day
    assert next_day["operation_id"] != first["operation_id"]
    assert len((tmp_path / "calls").read_text().splitlines()) == 4


def test_two_concurrent_processes_start_only_one_mock_batch(tmp_path):
    root, env, _, path = setup_root(tmp_path)
    gate = tmp_path / "start-gate"
    script = r"""
import json,sys,time
from pathlib import Path
from datetime import datetime
from bt_api_py import trading_calendar_schedule_once as s
s._now_utc=lambda: datetime.fromisoformat('2026-11-13T16:30:00+00:00')
Path(sys.argv[3]).touch()
deadline=time.monotonic()+10
while not Path(sys.argv[2]).exists() and time.monotonic()<deadline: time.sleep(.01)
value=s.run_once(config_file=sys.argv[1],filesystem=s.FS)
print(json.dumps({'final':value},ensure_ascii=True))
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(path), str(gate), str(tmp_path / f"ready-{index}")],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    try:
        import time

        deadline = time.monotonic() + 10
        while (
            not all((tmp_path / f"ready-{i}").exists() for i in range(2))
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert all((tmp_path / f"ready-{i}").exists() for i in range(2))
        gate.touch()
        results = []
        for proc in processes:
            stdout, stderr = proc.communicate(timeout=60)
            assert proc.returncode == 0, stderr
            results.append(json.loads(stdout.splitlines()[-1])["final"])
        assert sum(result["status"] == "COMMITTED" for result in results) == 1, results
        assert all(
            result["status"] in {"COMMITTED", "UNKNOWN", "ALREADY_COMPLETED"} for result in results
        )
        assert len((tmp_path / "calls").read_text().splitlines()) == 2
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


@pytest.mark.parametrize("mode", ["auto", "manual"])
@pytest.mark.parametrize("partial", [False, True])
def test_revalidated_batch_status_is_reported_and_missing_evidence_unknown(tmp_path, mode, partial):
    root, env, _, path = setup_root(tmp_path, mode=mode)
    if partial:
        env["FAIL_DCE"] = "1"
    result = execute(env, _schedule_subprocess(env, path))
    expected = "PARTIAL" if partial else "COMPLETE"
    assert result["status"] == ("COMMITTED" if mode == "auto" else "PENDING_REVIEW"), result
    assert result["batch_status"] == expected
    shown = execute(
        env, _schedule_subprocess(env, path, code="inspect") + [str(root), result["operation_id"]]
    )
    if mode == "auto":
        assert shown["terminal"]["batch_status"] == expected
    else:
        assert shown["status"] == "PENDING_REVIEW" and shown["batch_status"] == expected
    repeated = execute(env, _schedule_subprocess(env, path))
    assert repeated["status"] == ("ALREADY_COMPLETED" if mode == "auto" else "PENDING_REVIEW")
    assert repeated["operation_id"] == result["operation_id"]
    assert repeated["batch_status"] == expected
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    evidence = root / "observation-runs" / (
        "mock-daily-batch-" + result["identity_bundle"]["batch_id"] + ".json"
    )
    evidence.write_bytes(b"damaged test evidence")
    unknown = execute(env, _schedule_subprocess(env, path))
    assert unknown["status"] == "UNKNOWN" and unknown["operation_id"] == result["operation_id"]
    assert "batch_status" not in unknown
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


_DELETE_RESULT_AT_FINAL_CHECK = r"""
from bt_api_py import trading_calendar_schedule_once as scheduler
_original_schedule_verify = scheduler._verify_locked
def remove_result_before_locked_replay(store, pending, returned, proof):
    if os.environ.get('DELETE_SCHEDULE_RESULT'):
        from pathlib import Path
        oid=pending['identity_bundle']['observations'][0]['observation_id']
        (Path(pending['root'])/'akshare-observations'/('result-'+oid+'.json')).unlink()
        Path(os.environ['SCHEDULE_GUARD_MARK']).write_text('checked')
        os.environ.pop('DELETE_SCHEDULE_RESULT',None)
    return _original_schedule_verify(store,pending,returned,proof)
scheduler._verify_locked = remove_result_before_locked_replay
"""


def test_locked_terminal_replay_rejects_changed_observation(tmp_path):
    root, env, _, path = setup_root(tmp_path, extra=_DELETE_RESULT_AT_FINAL_CHECK)
    marker = tmp_path / "locked-replay"
    env.update(DELETE_SCHEDULE_RESULT="1", SCHEDULE_GUARD_MARK=str(marker))
    result = execute(env, _schedule_subprocess(env, path))
    assert marker.exists() and result["status"] == "UNKNOWN", result
    assert (root / "observation-runs" / schedule._PENDING_FILE).exists()
    assert not list((root / "observation-runs").glob("schedule-once-terminal-*.json"))
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


def test_manual_review_remains_pending_then_original_id_reconcile(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW", first
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_schedule_once",
            "--action",
            "inspect",
            "--root",
            str(root),
            "--operation-id",
            first["operation_id"],
            "--trusted-local-posix",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (
        proc.returncode == 10
        and json.loads(proc.stdout.splitlines()[-1])["status"] == "PENDING_REVIEW"
    )
    again = execute(env, _schedule_subprocess(env, path, date_utc="2026-11-15T00:00:00+00:00"))
    assert again["status"] == "PENDING_REVIEW" and again["operation_id"] == first["operation_id"]
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    ids = first["identity_bundle"]
    rc, rows, err = command(
        env,
        *operation(
            root,
            ids["run_id"],
            "mock-confirm",
            "--review-id",
            ids["review_id"],
            "--review-sha256",
            first["review_payload_sha256"],
            "--reviewer",
            "MOCK_reviewer",
            "--confirmed-at",
            "2026-11-13T00:00:00.000000Z",
        ),
    )
    assert rc == 0 and rows[-1]["status"] == "COMMITTED", (rows, err)
    after_confirm = execute(
        env, _schedule_subprocess(env, path, date_utc="2026-11-15T00:00:00+00:00")
    )
    assert (
        after_confirm["status"] == "UNKNOWN"
        and after_confirm["operation_id"] == first["operation_id"]
    )
    reconciled = execute(
        env, _schedule_subprocess(env, path, code="reconcile") + [str(root), first["operation_id"]]
    )
    assert reconciled["status"] == "COMMITTED", reconciled
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


def test_pending_review_inspect_never_creates_missing_store_layout(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW", first
    original = root / "versions"
    saved = root / "versions-saved"
    original.rename(saved)
    try:
        shown = execute(
            env,
            _schedule_subprocess(env, path, code="inspect") + [str(root), first["operation_id"]],
        )
        assert shown["status"] == "UNKNOWN"
        assert not original.exists()
    finally:
        saved.rename(original)


@pytest.mark.parametrize("lost", ["pending", "intent", "database", "marker", "both", "journal"])
def test_active_ledger_loss_never_restarts_original(tmp_path, monkeypatch, lost):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    calls = []

    def fake_batch(**kwargs):
        calls.append(1)
        return {
            "status": "UNKNOWN",
            "identity_bundle": kwargs["identity_bundle"],
            "requested_date": kwargs["requested_date"].isoformat(),
        }

    monkeypatch.setattr(schedule.batch, "run", fake_batch)
    first = call(path)
    assert first["status"] == "UNKNOWN" and calls == [1]
    folder = root / "observation-runs"
    names = {
        "pending": [schedule._PENDING_FILE],
        "intent": [schedule._name("intent", first["operation_id"])],
        "database": [schedule._LEDGER_FILE],
        "marker": [schedule._LEDGER_MARKER],
        "both": [schedule._LEDGER_FILE, schedule._LEDGER_MARKER],
    }
    if lost == "journal":
        (folder / (schedule._LEDGER_FILE + "-journal")).touch()
    else:
        for name in names[lost]:
            (folder / name).unlink()
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT.replace(day=15))
    again = call(path)
    assert again["status"] == "UNKNOWN" and calls == [1]
    if lost in {"pending", "intent", "both"}:
        assert again["operation_id"] == first["operation_id"]
        assert again["identity_bundle"] == first["identity_bundle"]


def test_legacy_artifact_prevents_new_empty_ledger(tmp_path, monkeypatch):
    root, _, _, path = setup_root(tmp_path)
    folder = root / "observation-runs"
    (folder / "schedule-once-intent-old.json").write_text("legacy")
    monkeypatch.setattr(schedule.batch, "run", lambda **kw: pytest.fail("unexpected batch"))
    assert call(path)["status"] == "UNKNOWN"
    assert not (folder / schedule._LEDGER_FILE).exists()


def test_completed_configuration_and_missing_evidence(tmp_path):
    root, env, config, path = setup_root(tmp_path)
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED", first
    config["budget_seconds"] = 59
    path.write_text(json.dumps(config))
    changed = execute(env, _schedule_subprocess(env, path))
    assert changed["status"] == "CONFIG_CHANGED_ORIGINAL_COMPLETED", changed
    assert changed["operation_id"] == first["operation_id"]
    assert changed["original_config_sha256"] != changed["current_config_sha256"]
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    oid = first["identity_bundle"]["observations"][0]["observation_id"]
    (root / "akshare-observations" / ("result-" + oid + ".json")).unlink()
    missing = execute(env, _schedule_subprocess(env, path))
    assert missing["status"] == "UNKNOWN" and missing["operation_id"] == first["operation_id"]
    shown = execute(
        env, _schedule_subprocess(env, path, code="inspect") + [str(root), first["operation_id"]]
    )
    assert shown["status"] == "LOCAL_HISTORICAL_RECORD_ONLY"
    assert shown["evidence_revalidation"] == "NOT_REVALIDATED"
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


def test_missing_date_index_blocks_same_and_next_day(tmp_path):
    root, env, config, path = setup_root(tmp_path)
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED", first
    (
        root / "observation-runs" / schedule._date_name(config["job_key"], first["requested_date"])
    ).unlink()
    for date in ("2026-11-13T16:30:00+00:00", "2026-11-15T00:00:00+00:00"):
        result = execute(env, _schedule_subprocess(env, path, date_utc=date))
        assert result["status"] == "UNKNOWN" and result["operation_id"] == first["operation_id"]
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


@pytest.mark.parametrize(
    "stage",
    ["fence", "marker", "insert_before", "insert_after", "pending", "intent", "unlink_after"],
)
def test_initialization_interrupt_keeps_original_fence(tmp_path, monkeypatch, stage):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    calls = []
    monkeypatch.setattr(schedule.batch, "run", lambda **kw: calls.append(kw))
    write = schedule._write
    insert = schedule._ledger_insert
    release = schedule._release_initial_fence

    def broken_write(fd, chain, name, value):
        result = write(fd, chain, name, value)
        targets = {
            "fence": schedule._INIT_FENCE,
            "marker": schedule._LEDGER_MARKER,
            "pending": schedule._PENDING_FILE,
        }
        if name == targets.get(stage) or (
            stage == "intent" and name.startswith("schedule-once-intent-")
        ):
            raise OSError("injected after durable write")
        return result

    def broken_insert(*args):
        if stage == "insert_before":
            raise OSError("before transaction")
        insert(*args)
        if stage == "insert_after":
            raise OSError("after transaction")

    def broken_release(*args):
        release(*args)
        if stage == "unlink_after":
            raise OSError("after fence unlink")

    monkeypatch.setattr(schedule, "_write", broken_write)
    monkeypatch.setattr(schedule, "_ledger_insert", broken_insert)
    monkeypatch.setattr(schedule, "_release_initial_fence", broken_release)
    first = call(path)
    assert first["status"] == "UNKNOWN" and first.get("operation_id") and not calls
    monkeypatch.setattr(schedule, "_write", write)
    monkeypatch.setattr(schedule, "_ledger_insert", insert)
    monkeypatch.setattr(schedule, "_release_initial_fence", release)
    again = call(path)
    assert again["status"] == "UNKNOWN" and again["operation_id"] == first["operation_id"]
    assert again["identity_bundle"] == first["identity_bundle"] and not calls
    shown = schedule.inspect(
        root=str(root), operation_id=first["operation_id"], filesystem=schedule.FS
    )
    assert shown["status"] == "UNKNOWN" and shown["operation_id"] == first["operation_id"]


def test_lost_initial_fence_with_empty_enabled_ledger_blocks(tmp_path, monkeypatch):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    monkeypatch.setattr(schedule.batch, "run", lambda **kw: pytest.fail("unexpected batch"))
    insert = schedule._ledger_insert
    monkeypatch.setattr(
        schedule, "_ledger_insert", lambda *args: (_ for _ in ()).throw(OSError("before insert"))
    )
    first = call(path)
    assert first["status"] == "UNKNOWN" and first.get("operation_id")
    (root / "observation-runs" / schedule._INIT_FENCE).unlink()
    monkeypatch.setattr(schedule, "_ledger_insert", insert)
    assert call(path)["status"] == "UNKNOWN"


@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_initial_schema_transaction_interrupt_keeps_original(tmp_path, monkeypatch, stage):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    monkeypatch.setattr(schedule.batch, "run", lambda **kw: pytest.fail("unexpected batch"))
    connect = schedule.sqlite3.connect
    faulted = []

    class FaultConnection:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, statement, *args):
            if statement == "COMMIT" and not faulted:
                faulted.append(stage)
                if stage == "before_commit":
                    raise OSError("before schema commit")
                self.connection.execute(statement, *args)
                raise OSError("after schema commit")
            return self.connection.execute(statement, *args)

    def fault_connect(database, *args, **kwargs):
        result = connect(database, *args, **kwargs)
        if schedule._LEDGER_FILE in str(database):
            return FaultConnection(result)
        return result

    monkeypatch.setattr(schedule.sqlite3, "connect", fault_connect)
    first = call(path)
    assert faulted == [stage] and first["status"] == "UNKNOWN" and first.get("operation_id")
    monkeypatch.setattr(schedule.sqlite3, "connect", connect)
    again = call(path)
    assert again["status"] == "UNKNOWN" and again["operation_id"] == first["operation_id"]
    assert again["identity_bundle"] == first["identity_bundle"]


def _active_read_command(root):
    script = r"""
import json,sys
from bt_api_py import trading_calendar_schedule_once as s
print(json.dumps({'final':s.inspect_active(root=sys.argv[1],filesystem=s.FS)}))
"""
    return [sys.executable, "-c", script, str(root)]


def _snapshot_tree(root):
    import hashlib

    return {
        str(path.relative_to(root)): (
            "directory" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mode,
        )
        for path in root.rglob("*")
    }


def _confirm_original_review(env, root, result):
    ids = result["identity_bundle"]
    rc, rows, err = command(
        env,
        *operation(
            root,
            ids["run_id"],
            "mock-confirm",
            "--review-id",
            ids["review_id"],
            "--review-sha256",
            result["review_payload_sha256"],
            "--reviewer",
            "MOCK_reviewer",
            "--confirmed-at",
            "2026-11-13T00:00:00.000000Z",
        ),
    )
    assert rc == 0 and rows[-1]["status"] == "COMMITTED", (rows, err)


def test_inspect_active_real_pending_confirmed_and_read_only(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="manual")
    initial = _snapshot_tree(root)
    unavailable = execute(env, _active_read_command(root))
    assert unavailable["status"] == "UNKNOWN" and _snapshot_tree(root) == initial
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    before = _snapshot_tree(root)
    found = execute(env, _active_read_command(root))
    ids = first["identity_bundle"]
    assert found["status"] == "PENDING_REVIEW", found
    assert (found["operation_id"], found["run_id"], found["review_id"]) == (
        first["operation_id"],
        ids["run_id"],
        ids["review_id"],
    )
    assert found["review_payload_sha256"] == first["review_payload_sha256"]
    assert (
        found["environment"] == "SIMULATION_ONLY" and found["source_authentication"] == "NOT_PROVEN"
    )
    assert found["notification_preconditions"] == "REVALIDATE_POLICY_SUPPLEMENT_CURRENT"
    assert found["notification_authorization"] == "NOT_PROVEN"
    assert found["state_freshness"] == "NOT_GUARANTEED_AFTER_LOCK_RELEASE"
    assert _snapshot_tree(root) == before
    _confirm_original_review(env, root, first)
    before = _snapshot_tree(root)
    confirmed = execute(env, _active_read_command(root))
    assert confirmed["status"] == "UNKNOWN" and confirmed["operation_id"] == first["operation_id"]
    assert _snapshot_tree(root) == before
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


def test_inspect_active_none_after_completed_and_no_creation(tmp_path):
    root, env, _, path = setup_root(tmp_path)
    absent = tmp_path / "no-root"
    assert schedule.inspect_active(root=str(absent), filesystem=schedule.FS)["status"] == "UNKNOWN"
    assert not absent.exists()
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED"
    before = _snapshot_tree(root)
    result = execute(env, _active_read_command(root))
    assert (
        result["status"] == "NONE"
        and result["state_freshness"] == "NOT_GUARANTEED_AFTER_LOCK_RELEASE"
    )
    assert _snapshot_tree(root) == before


@pytest.mark.parametrize(
    "missing", ["pending", "intent", "returned", "ledger", "marker", "observation", "store_lock"]
)
def test_inspect_active_missing_evidence_is_unknown(tmp_path, missing):
    root, env, _, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    folder = root / "observation-runs"
    names = {
        "pending": folder / schedule._PENDING_FILE,
        "intent": folder / schedule._name("intent", first["operation_id"]),
        "returned": folder / schedule._name("returned", first["operation_id"]),
        "ledger": folder / schedule._LEDGER_FILE,
        "marker": folder / schedule._LEDGER_MARKER,
        "store_lock": root / ".lock",
        "observation": root
        / "akshare-observations"
        / ("result-" + first["identity_bundle"]["observations"][0]["observation_id"] + ".json"),
    }
    names[missing].unlink()
    before = _snapshot_tree(root)
    result = execute(env, _active_read_command(root))
    assert result["status"] == "UNKNOWN", result
    if missing in {"pending", "intent", "returned", "observation"}:
        assert result["operation_id"] == first["operation_id"]
    assert _snapshot_tree(root) == before
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


def test_inspect_active_lock_busy_never_creates_or_waits(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    script = r"""
import sys
from bt_api_py.trading_calendar_store import TradingCalendarStore
with TradingCalendarStore(sys.argv[1])._writer_lock():
    print('ready',flush=True)
    sys.stdin.readline()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(root)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "ready"
        before = _snapshot_tree(root)
        result = execute(env, _active_read_command(root))
        assert result["status"] == "UNKNOWN"
        assert _snapshot_tree(root) == before
    finally:
        proc.communicate("done\n", timeout=15)
    assert proc.returncode == 0


def test_inspect_active_detects_cross_process_confirmation(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    script = r"""
import json,sys
from bt_api_py import trading_calendar_schedule_once as s
original=s._batch_proof
def paused(*args,**kwargs):
    value=original(*args,**kwargs)
    print('proof-ready',flush=True)
    sys.stdin.readline()
    return value
s._batch_proof=paused
print(json.dumps({'final':s.inspect_active(root=sys.argv[1],filesystem=s.FS)}),flush=True)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(root)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "proof-ready"
        _confirm_original_review(env, root, first)
        stdout, stderr = proc.communicate("continue\n", timeout=30)
        assert proc.returncode == 0, stderr
        result = json.loads(stdout.splitlines()[-1])["final"]
        assert result["status"] == "UNKNOWN" and result["operation_id"] == first["operation_id"]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


def test_inspect_active_started_without_return_is_unknown(tmp_path, monkeypatch):
    root, _, _, path = setup_root(tmp_path)
    monkeypatch.setattr(schedule, "_now_utc", lambda: INSTANT)
    calls = []

    def interrupted(**kwargs):
        calls.append(kwargs)
        raise OSError("batch outcome unknown")

    monkeypatch.setattr(schedule.batch, "run", interrupted)
    first = call(path)
    assert first["status"] == "UNKNOWN" and len(calls) == 1
    before = _snapshot_tree(root)
    result = schedule.inspect_active(root=str(root), filesystem=schedule.FS)
    assert result["status"] == "UNKNOWN" and result["operation_id"] == first["operation_id"]
    assert len(calls) == 1 and _snapshot_tree(root) == before


def test_inspect_active_conflicting_pending_identity_is_unknown(tmp_path):
    root, env, _, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    pending_path = root / "observation-runs" / schedule._PENDING_FILE
    payload, _ = schedule.storage._unwrap(pending_path.read_bytes())
    payload["operation_id"] = "f" * 32
    pending_path.write_bytes(schedule.storage._envelope(payload))
    before = _snapshot_tree(root)
    result = execute(env, _active_read_command(root))
    assert result["status"] == "UNKNOWN" and result["operation_id"] == first["operation_id"]
    assert _snapshot_tree(root) == before


def test_inspect_active_missing_last_identity_is_unknown(tmp_path, monkeypatch):
    root, env, _, path = setup_root(tmp_path)
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "COMMITTED"
    before = _snapshot_tree(root)
    monkeypatch.setattr(schedule, "_ledger_state", lambda *args: (None, None))
    result = schedule.inspect_active(root=str(root), filesystem=schedule.FS)
    assert result["status"] == "UNKNOWN" and _snapshot_tree(root) == before


@pytest.mark.parametrize("missing", ["review", "observation"])
def test_inspect_active_rechecks_proof_after_first_snapshot(tmp_path, missing):
    root, env, _, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW"
    ids = first["identity_bundle"]
    remove = (
        root / "review-bundles" / ("mock-review-" + ids["review_id"] + ".json")
        if missing == "review"
        else root
        / "akshare-observations"
        / ("result-" + ids["observations"][0]["observation_id"] + ".json")
    )
    script = r"""
import json,sys
from pathlib import Path
from bt_api_py import trading_calendar_schedule_once as s
original=s._batch_proof
removed=[]
def after_first(*args,**kwargs):
    value=original(*args,**kwargs)
    if not removed:
        Path(sys.argv[2]).unlink()
        removed.append(True)
    return value
s._batch_proof=after_first
result=s.inspect_active(root=sys.argv[1],filesystem=s.FS)
print(json.dumps({'final':result, 'removed':bool(removed)}),flush=True)
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(root), str(remove)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode == 0, process.stderr
    output = json.loads(process.stdout.splitlines()[-1])
    assert output["removed"]
    result = output["final"]
    assert result["status"] == "UNKNOWN" and result["operation_id"] == first["operation_id"]
    assert not remove.exists()
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


def test_inspect_active_rechecks_indexed_scope_under_existing_lock(tmp_path):
    root, env, config, path = setup_root(tmp_path, mode="manual")
    first = execute(env, _schedule_subprocess(env, path))
    assert first["status"] == "PENDING_REVIEW", first
    batch_id = first["identity_bundle"]["batch_id"]
    migration_id = "d" * 32
    rc, started, err = command(
        env,
        *operation(
            root,
            config["original_run_id"],
            "mock-scope-migrate-begin",
            "--migration-id",
            migration_id,
            "--legacy-scope-batch-id",
            batch_id,
            "--declare-old-writers-stopped",
        ),
    )
    assert rc != 0 and started[-1]["status"] == "PENDING_MIGRATION", (started, err)
    rc, resumed, err = command(
        env,
        *operation(
            root,
            config["original_run_id"],
            "mock-scope-migrate-resume",
            "--migration-id",
            migration_id,
            "--declare-old-writers-stopped",
        ),
    )
    assert rc == 0 and resumed[-1]["reason"] == "MIGRATION_READY", (resumed, err)
    before = _snapshot_tree(root)
    result = execute(env, _active_read_command(root))
    assert result["status"] == "PENDING_REVIEW" and result["operation_id"] == first["operation_id"]
    assert _snapshot_tree(root) == before
