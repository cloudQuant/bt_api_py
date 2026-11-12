"""Offline checks for claim-time budget fences."""

import json
import subprocess
import sys

import pytest

from bt_api_py import trading_calendar_detail_collection as collection
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_source_evidence as source
from tests.unit.test_trading_calendar_candidate_inbox import HOURS
from tests.unit.test_trading_calendar_ine_observation_run import holiday_page
from tests.unit.test_trading_calendar_ine_retry_kernel import IDS
from tests.unit.test_trading_calendar_observation_detail_collection import options
from tests.unit.test_trading_calendar_observation_run import cli, run_args, show
from tests.unit.test_trading_calendar_preview_catalog import setup_preview
from tests.unit.test_trading_calendar_preview_network_navigation import inject
from tests.unit.test_trading_calendar_preview_reports import report
from tests.unit.test_trading_calendar_source_evidence import fake_runner


class Clock:
    now = 10.0

    def monotonic(self):
        return self.now


@pytest.mark.parametrize("exchange", ["SHFE", "INE"])
def test_claim_budget_expires_before_transport(tmp_path, monkeypatch, exchange):
    clock = Clock()
    monkeypatch.setattr(source, "time", clock)
    lock = source.storage._lock
    count = []

    def delayed_lock(*args):
        result = lock(*args)
        count.append(1)
        if len(count) == 1:
            clock.now += 11
        return result

    monkeypatch.setattr(source.storage, "_lock", delayed_lock)
    calls = []
    result = source.capture(
        root=str(tmp_path),
        evidence_id="a" * 32,
        kind="hours",
        filesystem="TRUSTED_LOCAL_POSIX",
        emit=lambda _: None,
        exchange=exchange,
        monotonic_deadline=20,
        runner=lambda *_: calls.append(True),
    )
    assert result["status"] == "COMMITTED"
    assert result["reason"] == "NOT_STARTED_BUDGET_EXHAUSTED"
    assert calls == []
    assert (tmp_path / "source-evidence" / ("intent-" + "a" * 32 + ".json")).exists()
    observed = source.show(
        root=str(tmp_path), evidence_id="a" * 32, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert observed["status"] == "NOT_STARTED_BUDGET_EXHAUSTED"
    assert observed["payload_sha256"]
    assert (
        source.capture(
            root=str(tmp_path),
            evidence_id="a" * 32,
            kind="hours",
            filesystem="TRUSTED_LOCAL_POSIX",
            emit=lambda _: None,
            exchange=exchange,
            runner=lambda *_: calls.append(True),
        )["status"]
        != "COMMITTED"
    )
    assert calls == []


@pytest.mark.parametrize("exchange", ["SHFE", "INE"])
def test_post_claim_remaining_time_is_recorded(tmp_path, monkeypatch, exchange):
    clock = Clock()
    monkeypatch.setattr(source, "time", clock)
    lock = source.storage._lock
    count = []

    def delayed_lock(*args):
        result = lock(*args)
        count.append(1)
        if len(count) == 1:
            clock.now += 8
        return result

    monkeypatch.setattr(source.storage, "_lock", delayed_lock)
    args_seen = []
    if exchange == "INE":

        def runner(*args):
            args_seen.append(args)
            return "HTTP_OK", HOURS
    else:
        underlying = fake_runner(HOURS, [])

        def runner(*args):
            args_seen.append(args)
            return underlying(*args)

    result = source.capture(
        root=str(tmp_path),
        evidence_id="b" * 32,
        kind="hours",
        filesystem="TRUSTED_LOCAL_POSIX",
        emit=lambda _: None,
        exchange=exchange,
        monotonic_deadline=20,
        connect_timeout=5,
        read_timeout=10,
        total_timeout=20,
        runner=runner,
    )
    assert result["status"] == "COMMITTED"
    observed = source.show(
        root=str(tmp_path), evidence_id="b" * 32, filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert observed["status"] == "VISIBLE_UNAUTHENTICATED"
    assert observed["transport_settings"] == {
        "connect_timeout": 2,
        "read_timeout": 2,
        "total_timeout": 2,
    }
    assert len(args_seen) == 1
    numeric = args_seen[0][:3] if exchange == "INE" else args_seen[0][1:4]
    assert numeric == (2, 2, 2)
    record = tmp_path / "source-evidence" / ("result-" + "b" * 32 + ".json")
    body = json.loads(record.read_bytes())["body"]
    body["transport_settings"]["total_timeout"] = 21
    record.write_bytes(source.storage._envelope(body))
    assert (
        source.show(root=str(tmp_path), evidence_id="b" * 32, filesystem="TRUSTED_LOCAL_POSIX")[
            "status"
        ]
        == "UNKNOWN"
    )


@pytest.mark.parametrize("retry_enabled", [False, True])
def test_claim_exhaustion_is_visible_and_never_retried(tmp_path, monkeypatch, retry_enabled):
    root, _, config, supplement = setup_preview(tmp_path)
    clock = Clock()
    monkeypatch.setattr(runs, "time", clock)
    monkeypatch.setattr(source, "time", clock)
    monkeypatch.setattr(runs, "_supervise", lambda _: report())
    lock = source.storage._lock
    lock_calls = []

    def delayed_lock(*args):
        result = lock(*args)
        lock_calls.append(1)
        if len(lock_calls) == 1:
            clock.now += 200
        return result

    monkeypatch.setattr(source.storage, "_lock", delayed_lock)
    capture = source.capture
    calls = []

    def fake_capture(**kwargs):
        return capture(**kwargs, runner=lambda *_: calls.append(True))

    monkeypatch.setattr(source, "capture", fake_capture)
    retry = {"evidence_ids": IDS, "budget_seconds": 100, "interval_seconds": 600}
    first_ids = {kind: values[0] for kind, values in IDS.items()}
    result = runs.run_once(
        **run_args(root, config, supplement),
        ine_evidence_ids=first_ids,
        **({"ine_retry_config": retry} if retry_enabled else {}),
    )
    assert result["record_installation"] == "COMMITTED", result
    assert calls == []
    record = show(root)
    listing = runs.list_runs(root=str(root), filesystem="TRUSTED_LOCAL_POSIX")["runs"][0]
    if retry_enabled:
        assert result["ine_retry_stop_reason"] == "RETRY_BUDGET_EXHAUSTED"
        assert (
            record["observation"]["ine_retry_result"]["attempts"]["hours"][0]["state"]
            == "NOT_STARTED_BUDGET_EXHAUSTED"
        )
        assert record["observation"]["ine_retry_result"]["attempts"]["holiday"] == []
        assert (
            record["ine_retry_visibility"]["hours"][0]["visibility"]
            == "NOT_STARTED_BUDGET_EXHAUSTED"
        )
        assert (
            listing["ine_retry_visibility"]["hours"][0]["visibility"]
            == "NOT_STARTED_BUDGET_EXHAUSTED"
        )
    else:
        assert record["observation"]["schema"] == runs._RESULT_SCHEMA_V4
        assert (
            record["observation"]["ine_observations"]["hours"]["state"]
            == "NOT_STARTED_BUDGET_EXHAUSTED"
        )
        assert record["observation"]["ine_observations"]["holiday"]["state"] == "NOT_ATTEMPTED"
        assert record["ine_evidence_visibility"]["hours"] == "NOT_STARTED_BUDGET_EXHAUSTED"
        assert listing["ine_evidence_visibility"]["hours"] == "NOT_STARTED_BUDGET_EXHAUSTED"


def test_legacy_v4_with_v2_source_result_remains_readable(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda _: report())
    capture = source.capture
    collect = runs._ine_observations

    def old_collection(root, filesystem, ids, deadline, **kwargs):
        return collect(root, filesystem, ids, deadline)

    def fake_capture(**kwargs):
        raw = HOURS if kwargs["kind"] == "hours" else holiday_page()
        return capture(**kwargs, runner=lambda *_: ("HTTP_OK", raw))

    monkeypatch.setattr(runs, "_ine_observations", old_collection)
    monkeypatch.setattr(source, "capture", fake_capture)
    first = {kind: values[0] for kind, values in IDS.items()}
    outcome = runs.run_once(**run_args(root, config, supplement), ine_evidence_ids=first)
    assert outcome["record_installation"] == "COMMITTED"
    path = root / "source-evidence" / ("result-" + first["hours"] + ".json")
    assert json.loads(path.read_bytes())["body"]["schema"] == source._RESULT_INE
    observed = show(root)
    assert observed["observation"]["schema"] == runs._RESULT_SCHEMA_V4
    assert observed["ine_evidence_visibility"] == {
        "hours": "VISIBLE_UNAUTHENTICATED",
        "holiday": "VISIBLE_UNAUTHENTICATED",
    }


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("damage", ["remove_result", "alter_claim"])
def test_v4_detail_requires_every_attempted_source_visible(tmp_path, monkeypatch, legacy, damage):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["INE_HOLIDAY_FIXTURE"] = str(tmp_path / "holiday.fixture")
    env["INE_LEGACY"] = "1" if legacy else "0"
    (tmp_path / "holiday.fixture").write_bytes(holiday_page())
    site = tmp_path / "inject-network-nav" / "sitecustomize.py"
    with site.open("a") as file:
        file.write("""
from bt_api_py import trading_calendar_source_evidence as ine_evidence
original_ine_capture = ine_evidence.capture
def offline_ine_capture(**kwargs):
    if os.environ.get('INE_LEGACY') == '1': kwargs.pop('monotonic_deadline', None)
    result = ('NETWORK_TIMEOUT', None) if kwargs['kind'] == 'hours' else ('HTTP_OK', Path(os.environ['INE_HOLIDAY_FIXTURE']).read_bytes())
    return original_ine_capture(**kwargs, runner=lambda *_: result)
ine_evidence.capture = offline_ine_capture
if os.environ.get('INE_LEGACY') == '1':
    original_show = ine_evidence.show
    def legacy_show(**kwargs):
        result = original_show(**kwargs)
        if result.get('status') == 'VISIBLE_UNAUTHENTICATED': result['transport_settings'] = {}
        return result
    ine_evidence.show = legacy_show
""")
    prior = subprocess.run(
        cli(
            root,
            config,
            supplement,
            "--retain-candidate",
            "--fetch-product-navigation",
            "--enable-ine",
        ),
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert prior.returncode == 10, (prior.stdout, prior.stderr)
    allocation = json.loads(prior.stdout.splitlines()[0])
    run_id = allocation["run_id"]
    ids = allocation["ine_evidence_ids"]
    observed = runs.show_once(root=str(root), run_id=run_id, filesystem="TRUSTED_LOCAL_POSIX")
    assert [
        observed["observation"]["ine_observations"][kind]["state"] for kind in ("hours", "holiday")
    ] == ["FAILED", "PARTIAL_OBSERVATION"]
    result_path = root / "source-evidence" / ("result-" + ids["hours"] + ".json")
    expected_schema = source._RESULT_INE if legacy else source._RESULT_DEADLINE
    assert json.loads(result_path.read_bytes())["body"]["schema"] == expected_schema
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_detail_collection",
        "run",
        "--calendar-root",
        str(root),
        "--run-id",
        run_id,
        "--budget-seconds",
        "20",
        "--per-request-seconds",
        "3",
        "--trusted-local-posix",
    ]
    initial = subprocess.run(command, env=env, capture_output=True, text=True, timeout=40)
    assert initial.returncode == 0, (initial.stdout, initial.stderr)
    if damage == "remove_result":
        result_path.unlink()
        changed_kind = "hours"
    else:
        claim_path = root / "source-evidence" / ("intent-" + ids["holiday"] + ".json")
        claim_body = json.loads(claim_path.read_bytes())["body"]
        claim_body["total_timeout"] += 0.1
        claim_path.write_bytes(collection._envelope(claim_body))
        changed_kind = "holiday"
    assert (
        runs.show_once(root=str(root), run_id=run_id, filesystem="TRUSTED_LOCAL_POSIX")[
            "ine_evidence_visibility"
        ][changed_kind]
        == "UNKNOWN"
    )
    monkeypatch.setattr(collection.detail, "_supervise", lambda *_: pytest.fail("must not collect"))
    events = []
    refused = collection.collect_once(
        root=str(root),
        run_id=run_id,
        filesystem="TRUSTED_LOCAL_POSIX",
        budget_seconds=3,
        per_request_seconds=1,
        collection_id="f" * 32,
        emit=events.append,
    )
    assert refused["reason"] == "RUN_NOT_ELIGIBLE"
    assert events == []


@pytest.mark.parametrize("retry_enabled", [False, True])
def test_not_started_allows_independent_navigation_details(tmp_path, retry_enabled):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["NAV_MODE"] = "large"
    site = tmp_path / "inject-network-nav" / "sitecustomize.py"
    site.write_text(site.read_text().replace("range(80)", "range(9)"))
    with site.open("a") as file:
        file.write("""
from bt_api_py import trading_calendar_source_evidence as ine_evidence
original_ine_capture = ine_evidence.capture
class SourceClock:
    def __init__(self, deadline): self.deadline = deadline
    def monotonic(self): return self.deadline + 1
def offline_ine_capture(**kwargs):
    ine_evidence.time = SourceClock(kwargs['monotonic_deadline'])
    def no_transport(*args): raise AssertionError('unexpected transport')
    return original_ine_capture(**kwargs, runner=no_transport)
ine_evidence.capture = offline_ine_capture
""")
    completed = subprocess.run(
        cli(
            root,
            config,
            supplement,
            *options(),
            "--enable-ine",
            *(["--ine-retry-timeouts", "--ine-budget-seconds", "20"] if retry_enabled else []),
        ),
        env=env,
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert completed.returncode == 10, (completed.stdout, completed.stderr)
    lines = [json.loads(line) for line in completed.stdout.splitlines()]
    if retry_enabled:
        assert lines[1]["ine_retry_stop_reason"] == "RETRY_BUDGET_EXHAUSTED"
    assert lines[-1]["event"] == "DETAIL_COLLECTION_RESULT"
    assert lines[-1]["coverage"] == "NOT_PROVEN"
    observed = runs.show_once(
        root=str(root), run_id=lines[0]["run_id"], filesystem="TRUSTED_LOCAL_POSIX"
    )
    if retry_enabled:
        assert (
            observed["ine_retry_visibility"]["hours"][0]["visibility"]
            == "NOT_STARTED_BUDGET_EXHAUSTED"
        )
        first = lines[0]["ine_retry_config"]["evidence_ids"]["hours"][0]
    else:
        assert observed["ine_evidence_visibility"]["hours"] == "NOT_STARTED_BUDGET_EXHAUSTED"
        first = lines[0]["ine_evidence_ids"]["hours"]
    assert not (root / "CURRENT.json").exists()
    (root / "source-evidence" / ("result-" + first + ".json")).unlink()
    refused = collection.collect_once(
        root=str(root),
        run_id=lines[0]["run_id"],
        filesystem="TRUSTED_LOCAL_POSIX",
        budget_seconds=3,
        per_request_seconds=1,
        collection_id="f" * 32,
        emit=lambda _: None,
    )
    assert refused["status"] == "ERROR"
