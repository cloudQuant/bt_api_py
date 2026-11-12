"""Offline tests for the not-yet-integrated INE retry kernel."""

import json
import subprocess

import pytest

from bt_api_py import trading_calendar_detail_collection as collection
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_source_evidence as evidence
from tests.unit.test_trading_calendar_candidate_inbox import HOURS
from tests.unit.test_trading_calendar_ine_observation_run import holiday_page
from tests.unit.test_trading_calendar_observation_detail_collection import options
from tests.unit.test_trading_calendar_observation_run import cli, run_args, show
from tests.unit.test_trading_calendar_preview_catalog import setup_preview
from tests.unit.test_trading_calendar_preview_network_navigation import inject
from tests.unit.test_trading_calendar_preview_reports import report

IDS = {"hours": ["1" * 32, "2" * 32, "3" * 32], "holiday": ["4" * 32, "5" * 32, "6" * 32]}


def test_retry_waits_and_stops_after_per_source_success(monkeypatch):
    now = [0.0]
    sleeps = []
    calls = []
    monkeypatch.setattr(runs.time, "monotonic", lambda: now[0])

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    def collect(root, filesystem, ids, deadline, *, _only, _deadline_capture):
        calls.append((_only, ids[_only], now[0]))
        row = runs._ine_unstarted(ids[_only])
        timeout = ids[_only] in (IDS["hours"][0], IDS["holiday"][0])
        row.update(
            state="FAILED" if timeout else "PARTIAL_OBSERVATION",
            capture="COMMITTED",
            claim="COMMITTED",
            result_commit="COMMITTED",
            transport="NETWORK_TIMEOUT" if timeout else "HTTP_OK",
            parse="NETWORK_TIMEOUT" if timeout else "CANDIDATE",
            payload_sha256="a" * 64,
            claim_sha256="b" * 64,
        )
        return {_only: row}

    monkeypatch.setattr(runs.time, "sleep", sleep)
    monkeypatch.setattr(runs, "_ine_observations", collect)
    monkeypatch.setattr(
        runs,
        "_ine_visibility",
        lambda root, filesystem, obs: dict.fromkeys(
            obs["ine_observations"], "VISIBLE_UNAUTHENTICATED"
        ),
    )
    result = runs._ine_retry_observations("unused", "unused", IDS, 1300, 600)
    assert result["stop_reason"] == "NONE"
    assert sleeps == [600, 600]
    assert calls == [
        ("hours", IDS["hours"][0], 0),
        ("hours", IDS["hours"][1], 600),
        ("holiday", IDS["holiday"][0], 600),
        ("holiday", IDS["holiday"][1], 1200),
    ]


def test_two_sources_each_receive_at_most_two_retries(monkeypatch):
    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    calls = []
    monkeypatch.setattr(runs, "time", clock)

    def collect(root, filesystem, ids, deadline, *, _only, _deadline_capture):
        calls.append((_only, ids[_only], clock.now))
        row = runs._ine_unstarted(ids[_only])
        row.update(
            state="FAILED",
            capture="COMMITTED",
            claim="COMMITTED",
            result_commit="COMMITTED",
            transport="NETWORK_TIMEOUT",
            parse="NETWORK_TIMEOUT",
            payload_sha256="a" * 64,
            claim_sha256="b" * 64,
        )
        return {_only: row}

    monkeypatch.setattr(runs, "_ine_observations", collect)
    monkeypatch.setattr(
        runs,
        "_ine_visibility",
        lambda root, filesystem, obs: dict.fromkeys(
            obs["ine_observations"], "VISIBLE_UNAUTHENTICATED"
        ),
    )
    result = runs._ine_retry_observations("unused", "unused", IDS, 3000, 600)
    assert result["stop_reason"] == "NONE"
    assert calls == [
        ("hours", IDS["hours"][0], 0),
        ("hours", IDS["hours"][1], 600),
        ("hours", IDS["hours"][2], 1200),
        ("holiday", IDS["holiday"][0], 1200),
        ("holiday", IDS["holiday"][1], 1800),
        ("holiday", IDS["holiday"][2], 2400),
    ]


def test_real_evidence_timeout_and_retry_uses_distinct_claims(tmp_path, monkeypatch):
    root, _, _, _ = setup_preview(tmp_path)
    capture = evidence.capture
    calls = []

    def fake_capture(**kwargs):
        def runner(*_):
            claim = root / "source-evidence" / ("intent-" + kwargs["evidence_id"] + ".json")
            assert claim.exists()
            calls.append((kwargs["kind"], kwargs["evidence_id"]))
            if kwargs["evidence_id"] in (IDS["hours"][0], IDS["holiday"][0]):
                return "NETWORK_TIMEOUT", None
            return "HTTP_OK", HOURS if kwargs["kind"] == "hours" else holiday_page()

        return capture(**kwargs, runner=runner)

    monkeypatch.setattr(evidence, "capture", fake_capture)
    result = runs._ine_retry_observations(str(root), "TRUSTED_LOCAL_POSIX", IDS, 30, 0)
    assert result["stop_reason"] == "NONE"
    assert calls == [
        ("hours", IDS["hours"][0]),
        ("hours", IDS["hours"][1]),
        ("holiday", IDS["holiday"][0]),
        ("holiday", IDS["holiday"][1]),
    ]
    for kind, rows in result["attempts"].items():
        assert [row["state"] for row in rows] == ["FAILED", "PARTIAL_OBSERVATION"]
        assert all(row["claim_sha256"] and row["payload_sha256"] for row in rows)
        assert all(
            evidence.show(
                root=str(root), evidence_id=row["evidence_id"], filesystem="TRUSTED_LOCAL_POSIX"
            )["kind"]
            == kind
            for row in rows
        )
    assert not (root / "CURRENT.json").exists()


@pytest.mark.parametrize("transport", ["CHILD_TIMEOUT", "CHILD_ERROR", "CHILD_CLEANUP_UNCERTAIN"])
def test_unknown_stops_all_sources(tmp_path, monkeypatch, transport):
    root, _, _, _ = setup_preview(tmp_path)
    capture = evidence.capture
    calls = []

    def fake_capture(**kwargs):
        calls.append(kwargs["kind"])
        return capture(**kwargs, runner=lambda *_: (transport, None))

    monkeypatch.setattr(evidence, "capture", fake_capture)
    result = runs._ine_retry_observations(str(root), "TRUSTED_LOCAL_POSIX", IDS, 30, 0)
    assert result["stop_reason"] == "INE_CLEANUP_OR_RESULT_UNCERTAIN"
    assert calls == ["hours"]


def test_budget_does_not_start_retry(tmp_path, monkeypatch):
    root, _, _, _ = setup_preview(tmp_path)
    capture = evidence.capture
    calls = []

    def fake_capture(**kwargs):
        calls.append(kwargs["evidence_id"])
        return capture(**kwargs, runner=lambda *_: ("NETWORK_TIMEOUT", None))

    monkeypatch.setattr(evidence, "capture", fake_capture)
    result = runs._ine_retry_observations(str(root), "TRUSTED_LOCAL_POSIX", IDS, 1, 600)
    assert result["stop_reason"] == "RETRY_BUDGET_EXHAUSTED"
    assert calls == [IDS["hours"][0]]


def test_v5_run_persists_intent_before_capture_and_rechecks_every_attempt(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda _: report())
    capture = evidence.capture
    retry = {"evidence_ids": IDS, "budget_seconds": 30, "interval_seconds": 0}
    calls = []

    def fake_capture(**kwargs):
        intent = runs._valid_intent(
            (root / "observation-runs" / ("run-" + "a" * 32 + ".intent")).read_bytes(), "a" * 32
        )
        assert intent["ine_retry_config"] == retry
        calls.append(kwargs["evidence_id"])
        timeout = kwargs["evidence_id"] in (IDS["hours"][0], IDS["holiday"][0])
        return capture(
            **kwargs,
            runner=lambda *_: (
                ("NETWORK_TIMEOUT", None)
                if timeout
                else ("HTTP_OK", HOURS if kwargs["kind"] == "hours" else holiday_page())
            ),
        )

    monkeypatch.setattr(evidence, "capture", fake_capture)
    outcome = runs.run_once(
        **run_args(root, config, supplement),
        ine_evidence_ids={kind: values[0] for kind, values in IDS.items()},
        ine_retry_config=retry,
    )
    assert outcome["record_installation"] == "COMMITTED", outcome
    assert calls == [IDS["hours"][0], IDS["hours"][1], IDS["holiday"][0], IDS["holiday"][1]]
    result = show(root)
    assert result["observation"]["schema"] == runs._RESULT_SCHEMA_V5
    assert result["observation"]["publication"] == "NOT_AUTHORIZED"
    assert [x["visibility"] for x in result["ine_retry_visibility"]["hours"]] == [
        "VISIBLE_UNAUTHENTICATED",
        "VISIBLE_UNAUTHENTICATED",
        "NOT_RECORDED",
    ]
    claim = root / "source-evidence" / ("intent-" + IDS["hours"][1] + ".json")
    original = claim.read_bytes()
    body = json.loads(original)["body"]
    body["total_timeout"] = 0.1
    claim.write_bytes(collection._envelope(body))
    assert show(root)["ine_retry_visibility"]["hours"][1]["visibility"] == "UNKNOWN"
    claim.write_bytes(original)
    (root / "source-evidence" / ("result-" + IDS["hours"][0] + ".json")).unlink()
    assert show(root)["ine_retry_visibility"]["hours"][0]["visibility"] == "UNKNOWN"
    assert (
        runs.list_runs(root=str(root), filesystem="TRUSTED_LOCAL_POSIX")["runs"][0][
            "ine_retry_visibility"
        ]["hours"][0]["visibility"]
        == "UNKNOWN"
    )


def test_interrupt_during_wait_stops_further_requests(monkeypatch):
    class Clock:
        def monotonic(self):
            return 0.0

        def sleep(self, seconds):
            raise KeyboardInterrupt

    monkeypatch.setattr(runs, "time", Clock())
    calls = []

    def collect(root, filesystem, ids, deadline, *, _only, _deadline_capture):
        calls.append(ids[_only])
        row = runs._ine_unstarted(ids[_only])
        row.update(
            state="FAILED",
            capture="COMMITTED",
            claim="COMMITTED",
            result_commit="COMMITTED",
            transport="NETWORK_TIMEOUT",
            parse="NETWORK_TIMEOUT",
            payload_sha256="a" * 64,
            claim_sha256="b" * 64,
        )
        return {_only: row}

    monkeypatch.setattr(runs, "_ine_observations", collect)
    monkeypatch.setattr(
        runs,
        "_ine_visibility",
        lambda root, filesystem, obs: dict.fromkeys(
            obs["ine_observations"], "VISIBLE_UNAUTHENTICATED"
        ),
    )
    result = runs._ine_retry_observations("unused", "unused", IDS, 2000, 600)
    assert result["stop_reason"] == "WAIT_INTERRUPTED"
    assert calls == [IDS["hours"][0]]


def test_orphan_intent_does_not_auto_resume(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    run_id = "a" * 32
    first_ids = {kind: values[0] for kind, values in IDS.items()}
    retry = {"evidence_ids": IDS, "budget_seconds": 3000, "interval_seconds": 600}
    fds, chain = runs._open_runs(str(root), create=True)
    try:
        intent = runs._intent(run_id, False, "", False, first_ids, retry)
        assert runs._write_once(fds[-1], chain, f"run-{run_id}.intent", intent)[0] == "COMMITTED"
    finally:
        runs._close(fds)
    observed = show(root)
    assert observed["record"] == "NOT_OBSERVED"
    assert all(
        entry["visibility"] == "UNKNOWN"
        for entries in observed["ine_retry_visibility"].values()
        for entry in entries
    )
    monkeypatch.setattr(runs, "_supervise", lambda _: pytest.fail("orphan must not resume"))
    outcome = runs.run_once(
        **run_args(root, config, supplement), ine_evidence_ids=first_ids, ine_retry_config=retry
    )
    assert outcome["intent_installation"] != "COMMITTED"


@pytest.mark.parametrize("changed", ["result_removed", "claim_tampered"])
def test_retry_wait_rechecks_preceding_evidence(tmp_path, monkeypatch, changed):
    root, _, config, supplement = setup_preview(tmp_path)

    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            folder = root / "source-evidence"
            if changed == "result_removed":
                (folder / ("result-" + IDS["hours"][0] + ".json")).unlink()
            else:
                path = folder / ("intent-" + IDS["hours"][0] + ".json")
                body = json.loads(path.read_bytes())["body"]
                body["total_timeout"] = 0.1
                path.write_bytes(collection._envelope(body))
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(runs, "time", clock)
    monkeypatch.setattr(evidence, "time", clock)
    monkeypatch.setattr(runs, "_supervise", lambda _: report())
    capture = evidence.capture
    calls = []

    def fake_capture(**kwargs):
        calls.append(kwargs["evidence_id"])
        return capture(**kwargs, runner=lambda *_: ("NETWORK_TIMEOUT", None))

    monkeypatch.setattr(evidence, "capture", fake_capture)
    retry = {"evidence_ids": IDS, "budget_seconds": 2000, "interval_seconds": 600}
    first = {kind: values[0] for kind, values in IDS.items()}
    result = runs.run_once(
        **run_args(root, config, supplement), ine_evidence_ids=first, ine_retry_config=retry
    )
    assert result["record_installation"] == "COMMITTED", result
    assert result["ine_outcome"] == "UNKNOWN"
    assert result["ine_retry_stop_reason"] == "INE_EVIDENCE_CHANGED_OR_MISSING"
    assert calls == [IDS["hours"][0]]
    observed = show(root)
    assert observed["observation"]["ine_retry_result"]["attempts"]["hours"][0]["state"] == "FAILED"
    assert observed["ine_retry_visibility"]["hours"][0]["visibility"] == "UNKNOWN"
    assert observed["observation"]["ine_retry_result"]["attempts"]["holiday"] == []


@pytest.mark.parametrize("transport", ["HTTP_ERROR", "NETWORK_ERROR", "TLS_ERROR"])
def test_unclassified_failure_is_not_retried(tmp_path, monkeypatch, transport):
    root, _, _, _ = setup_preview(tmp_path)
    capture = evidence.capture
    calls = []

    def fake_capture(**kwargs):
        calls.append(kwargs["kind"])
        return capture(**kwargs, runner=lambda *_: (transport, None))

    monkeypatch.setattr(evidence, "capture", fake_capture)
    result = runs._ine_retry_observations(str(root), "TRUSTED_LOCAL_POSIX", IDS, 30, 0)
    assert calls == ["hours", "holiday"]
    assert result["stop_reason"] == "NONE"


def test_cli_v5_navigation_and_details_offline(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["NAV_MODE"] = "large"
    env["INE_HOURS_FIXTURE"] = str(tmp_path / "hours.fixture")
    env["INE_HOLIDAY_FIXTURE"] = str(tmp_path / "holiday.fixture")
    (tmp_path / "hours.fixture").write_bytes(HOURS)
    (tmp_path / "holiday.fixture").write_bytes(holiday_page())
    site = tmp_path / "inject-network-nav" / "sitecustomize.py"
    site.write_text(site.read_text().replace("range(80)", "range(9)"))
    with site.open("a") as file:
        file.write("""
from bt_api_py import trading_calendar_source_evidence as ine_evidence
original_ine_capture = ine_evidence.capture
def offline_ine_capture(**kwargs):
    path = os.environ['INE_HOURS_FIXTURE' if kwargs['kind'] == 'hours' else 'INE_HOLIDAY_FIXTURE']
    return original_ine_capture(**kwargs, runner=lambda *_: ('HTTP_OK', Path(path).read_bytes()))
ine_evidence.capture = offline_ine_capture
""")
    completed = subprocess.run(
        cli(
            root,
            config,
            supplement,
            *options(),
            "--enable-ine",
            "--ine-retry-timeouts",
            "--ine-budget-seconds",
            "20",
        ),
        env=env,
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert completed.returncode == 10, (completed.stdout, completed.stderr)
    lines = [json.loads(line) for line in completed.stdout.splitlines()]
    assert lines[0]["ine_retry_config"]["interval_seconds"] == 600
    assert len(lines[0]["ine_retry_config"]["evidence_ids"]["hours"]) == 3
    assert lines[1]["record_installation"] == "COMMITTED"
    assert lines[-1]["event"] == "DETAIL_COLLECTION_RESULT"
    assert lines[-1]["coverage"] == "NOT_PROVEN"
    observed = runs.show_once(
        root=str(root), run_id=lines[0]["run_id"], filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert observed["observation"]["schema"] == runs._RESULT_SCHEMA_V5
    assert observed["observation"]["candidate_id"] == lines[0]["candidate_id"]
    assert not (root / "CURRENT.json").exists()
    first = lines[0]["ine_retry_config"]["evidence_ids"]["hours"][0]
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


def test_cli_unknown_prevents_other_source_and_manual_details(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["NAV_MODE"] = "large"
    env["INE_CALLS"] = str(tmp_path / "ine-calls")
    site = tmp_path / "inject-network-nav" / "sitecustomize.py"
    site.write_text(site.read_text().replace("range(80)", "range(9)"))
    with site.open("a") as file:
        file.write("""
from bt_api_py import trading_calendar_source_evidence as ine_evidence
original_ine_capture = ine_evidence.capture
def offline_ine_capture(**kwargs):
    with open(os.environ['INE_CALLS'], 'a') as marker: marker.write(kwargs['kind'] + '\\n')
    return original_ine_capture(**kwargs, runner=lambda *_: ('CHILD_TIMEOUT', None))
ine_evidence.capture = offline_ine_capture
""")
    completed = subprocess.run(
        cli(
            root,
            config,
            supplement,
            *options(),
            "--enable-ine",
            "--ine-retry-timeouts",
            "--ine-budget-seconds",
            "20",
        ),
        env=env,
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert completed.returncode == 23, (completed.stdout, completed.stderr)
    lines = [json.loads(line) for line in completed.stdout.splitlines()]
    assert lines[1]["cleanup_status"] == "UNCERTAIN"
    assert lines[-1]["event"] == "DETAIL_COLLECTION_NOT_ATTEMPTED"
    assert (tmp_path / "ine-calls").read_text().splitlines() == ["hours"]
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
