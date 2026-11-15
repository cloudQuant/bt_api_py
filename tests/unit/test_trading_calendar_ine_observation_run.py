"""Offline INE observations through actual local evidence retention."""

import json
import runpy
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from bt_api_py import trading_calendar_detail_collection as collection
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_source_evidence as evidence
from tests.unit.test_trading_calendar_candidate_inbox import HOURS
from tests.unit.test_trading_calendar_detail_collection import CID, invoke, setup_fake
from tests.unit.test_trading_calendar_observation_navigation import flags
from tests.unit.test_trading_calendar_observation_run import cli, run_args, show
from tests.unit.test_trading_calendar_preview_catalog import setup_preview
from tests.unit.test_trading_calendar_preview_network_navigation import inject
from tests.unit.test_trading_calendar_preview_reports import report
from tests.unit.test_trading_calendar_preview_reports import report as disabled_report

IDS = {"hours": "b" * 32, "holiday": "c" * 32}


def holiday_page():
    path = (
        Path(__file__).resolve().parents[2]
        / "bt_api/bt_api_ctp/tests/test_calendar_ine_holiday_fetch.py"
    )
    return runpy.run_path(str(path))["_page"]()


def test_real_retention_records_and_rechecks_both_sources(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda _: disabled_report())
    actual_capture = evidence.capture
    calls = []

    def capture(**kwargs):
        # Intent is installed before any source starts.
        intent = runs._valid_intent(
            (root / "observation-runs" / ("run-" + "a" * 32 + ".intent")).read_bytes(), "a" * 32
        )
        assert intent["ine_evidence_ids"] == IDS
        calls.append(kwargs["kind"])
        raw = HOURS if kwargs["kind"] == "hours" else holiday_page()

        def local_transport(*_):
            claim = root / "source-evidence" / ("intent-" + kwargs["evidence_id"] + ".json")
            assert claim.exists()
            return "HTTP_OK", raw

        return actual_capture(**kwargs, runner=local_transport)

    monkeypatch.setattr(evidence, "capture", capture)
    outcome = runs.run_once(**run_args(root, config, supplement), ine_evidence_ids=IDS)
    assert outcome["record_installation"] == "COMMITTED"
    assert calls == ["hours", "holiday"]
    source = root / "source-evidence" / ("result-" + IDS["hours"] + ".json")
    original = source.read_bytes()
    source.write_bytes(original + b"tampered")
    assert show(root)["ine_evidence_visibility"]["hours"] == "UNKNOWN"
    source.write_bytes(original)
    claim = root / "source-evidence" / ("intent-" + IDS["hours"] + ".json")
    original_claim = claim.read_bytes()
    claim_body = json.loads(original_claim)["body"]
    # Keep the actual timeout valid so this specifically checks the retained
    # claim digest rather than only the transport/claim consistency check.
    claim_body["total_timeout"] += 0.1
    claim.write_bytes(collection._envelope(claim_body))
    assert (
        evidence.show(root=str(root), evidence_id=IDS["hours"], filesystem="TRUSTED_LOCAL_POSIX")[
            "status"
        ]
        == "VISIBLE_UNAUTHENTICATED"
    )
    assert show(root)["ine_evidence_visibility"]["hours"] == "UNKNOWN"
    assert (
        runs.list_runs(root=str(root), filesystem="TRUSTED_LOCAL_POSIX")["runs"][0][
            "ine_evidence_visibility"
        ]["hours"]
        == "UNKNOWN"
    )
    claim.write_bytes(original_claim)
    observed = show(root)
    assert observed["ine_evidence_visibility"] == {
        "hours": "VISIBLE_UNAUTHENTICATED",
        "holiday": "VISIBLE_UNAUTHENTICATED",
    }
    body = observed["observation"]
    assert body["schema"] == runs._RESULT_SCHEMA_V4
    assert body["venues"][1]["state"] == "PARTIAL_OBSERVATION"
    assert body["last_success"] == "NOT_UPDATED" and body["publication"] == "NOT_AUTHORIZED"
    assert body["ine_retry"] == "D67_20_GAP"
    assert not (root / "CURRENT.json").exists()
    assert (
        runs.run_once(**run_args(root, config, supplement), ine_evidence_ids=IDS)[
            "record_installation"
        ]
        != "COMMITTED"
    )
    assert calls == ["hours", "holiday"]
    (root / "source-evidence" / ("result-" + IDS["holiday"] + ".json")).unlink()
    later = show(root)
    assert later["ine_evidence_visibility"]["holiday"] == "UNKNOWN"
    assert later["observation"]["ine_observations"]["holiday"]["state"] == "PARTIAL_OBSERVATION"
    listing = runs.list_runs(root=str(root), filesystem="TRUSTED_LOCAL_POSIX")
    assert listing["runs"][0]["ine_evidence_visibility"]["holiday"] == "UNKNOWN"


@pytest.mark.parametrize("transport", ["CHILD_CLEANUP_UNCERTAIN", "CHILD_ERROR", "CHILD_TIMEOUT"])
def test_unknown_cleanup_stops_second_source(tmp_path, monkeypatch, transport):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda _: disabled_report())
    actual_capture = evidence.capture
    calls = []

    def capture(**kwargs):
        calls.append(kwargs["kind"])
        return actual_capture(**kwargs, runner=lambda *_: (transport, None))

    monkeypatch.setattr(evidence, "capture", capture)
    assert (
        runs.run_once(**run_args(root, config, supplement), ine_evidence_ids=IDS)[
            "record_installation"
        ]
        == "COMMITTED"
    )
    obs = show(root)["observation"]["ine_observations"]
    assert obs["hours"]["state"] == "UNKNOWN"
    assert obs["holiday"]["state"] == "NOT_ATTEMPTED"
    assert calls == ["hours"]


def test_access_challenge_and_network_failure_are_not_success(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda _: disabled_report())
    actual_capture = evidence.capture

    def capture(**kwargs):
        response = (
            ("HTTP_OK", "人机识别 应用防火墙".encode())
            if kwargs["kind"] == "hours"
            else ("NETWORK_ERROR", None)
        )
        return actual_capture(**kwargs, runner=lambda *_: response)

    monkeypatch.setattr(evidence, "capture", capture)
    assert (
        runs.run_once(**run_args(root, config, supplement), ine_evidence_ids=IDS)[
            "record_installation"
        ]
        == "COMMITTED"
    )
    body = show(root)["observation"]
    assert body["ine_observations"]["hours"]["state"] == "FAILED"
    assert body["ine_observations"]["holiday"]["state"] == "FAILED"
    assert body["venues"][1]["state"] == "FAILED"


def test_unproven_capture_never_upgraded_even_if_show_claims_visible(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda _: disabled_report())
    monkeypatch.setattr(evidence, "capture", lambda **_: {"status": "COMMIT_UNKNOWN"})
    monkeypatch.setattr(evidence, "show", lambda **_: {"status": "VISIBLE_UNAUTHENTICATED"})
    assert (
        runs.run_once(**run_args(root, config, supplement), ine_evidence_ids=IDS)[
            "record_installation"
        ]
        == "COMMITTED"
    )
    result = show(root)
    assert result["observation"]["ine_observations"]["hours"]["state"] == "UNKNOWN"
    assert result["ine_evidence_visibility"]["hours"] == "UNKNOWN"


def test_tampered_ine_result_rejected(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda _: disabled_report())
    monkeypatch.setattr(evidence, "capture", lambda **_: {"status": "COMMIT_UNKNOWN"})
    assert (
        runs.run_once(**run_args(root, config, supplement), ine_evidence_ids=IDS)[
            "record_installation"
        ]
        == "COMMITTED"
    )
    path = root / "observation-runs" / ("run-" + "a" * 32 + ".json")
    record = json.loads(path.read_bytes())
    record["body"]["ine_observations"]["hours"]["state"] = "PARTIAL_OBSERVATION"
    path.write_bytes(runs._canonical(record))
    assert show(root)["record"] == "UNKNOWN"


def test_budget_exhaustion_never_starts_ine(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)

    def slow_preview(_):
        time.sleep(0.03)
        return disabled_report()

    monkeypatch.setattr(runs, "_supervise", slow_preview)
    monkeypatch.setattr(evidence, "capture", lambda **_: pytest.fail("budget exhausted"))
    args = run_args(root, config, supplement)
    args["budget_seconds"] = 0.001
    result = runs.run_once(**args, ine_evidence_ids=IDS)
    assert result["record_installation"] == "COMMITTED"
    assert show(root)["observation"]["venues"][1]["state"] == "NOT_ATTEMPTED"


def test_capture_exception_stops_and_retains_original_ids(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda _: disabled_report())
    calls = []

    def interrupted(**kwargs):
        calls.append(kwargs["evidence_id"])
        raise KeyboardInterrupt

    monkeypatch.setattr(evidence, "capture", interrupted)
    result = runs.run_once(**run_args(root, config, supplement), ine_evidence_ids=IDS)
    assert result["record_installation"] == "COMMITTED"
    assert result["reason"] == "INE_CLEANUP_UNCERTAIN"
    assert result["cleanup_status"] == "UNCERTAIN"
    observed = show(root)
    assert observed["ine_evidence_ids"] == IDS
    assert observed["observation"]["ine_observations"]["hours"]["state"] == "UNKNOWN"
    assert observed["observation"]["ine_observations"]["holiday"]["state"] == "NOT_ATTEMPTED"
    assert calls == [IDS["hours"]]


def test_uncertain_navigation_cleanup_prevents_ine_request(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    candidate = "d" * 32
    preview = replace(
        report(),
        candidate_id=candidate,
        candidate_status="COMMIT_UNKNOWN",
        candidate_reason="CHILD_CLEANUP_UNCERTAIN",
    )
    monkeypatch.setattr(runs, "_supervise", lambda _: preview)
    monkeypatch.setattr(evidence, "capture", lambda **_: pytest.fail("INE must not start"))
    result = runs.run_once(
        **run_args(root, config, supplement),
        candidate_id=candidate,
        fetch_product_navigation=True,
        ine_evidence_ids=IDS,
    )
    assert result["record_installation"] == "COMMITTED"
    assert result["reason"] == "PREVIEW_CLEANUP_UNCERTAIN"
    assert result["cleanup_status"] == "UNCERTAIN"
    assert all(
        item["state"] == "NOT_ATTEMPTED"
        for item in show(root)["observation"]["ine_observations"].values()
    )


@pytest.mark.parametrize(
    "mode", ["interrupt", "system_exit", "attempt_unknown", "commit_unknown", "cleanup"]
)
def test_cli_uncertain_ine_never_starts_details_or_retries(tmp_path, mode):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["INE_FAULT"] = mode
    env["INE_MARKER"] = str(tmp_path / "ine-calls")
    with (tmp_path / "inject-network-nav" / "sitecustomize.py").open("a") as file:
        file.write("""
if "--_worker" not in sys.argv:
    import bt_api_py.trading_calendar_source_evidence as ev
    original_capture = ev.capture
    def broken_capture(**kwargs):
        marker = Path(os.environ["INE_MARKER"])
        marker.write_text((marker.read_text() if marker.exists() else "") + kwargs["kind"] + "\\n")
        mode = os.environ["INE_FAULT"]
        if mode == "interrupt": raise KeyboardInterrupt()
        if mode == "system_exit": raise SystemExit(2)
        if mode == "attempt_unknown": return {"status": "ATTEMPT_UNKNOWN"}
        if mode == "commit_unknown": return {"status": "COMMIT_UNKNOWN"}
        return original_capture(**kwargs, runner=lambda *_: ("CHILD_CLEANUP_UNCERTAIN", None))
    ev.capture = broken_capture
""")
    cmd = cli(
        root,
        config,
        supplement,
        *flags(),
        "--enable-ine",
        "--collect-product-details",
        "--detail-budget-seconds",
        "10",
        "--detail-per-request-seconds",
        "2",
    )
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 23, (result.stdout, result.stderr)
    allocation, outcome, skipped = map(json.loads, result.stdout.splitlines())
    assert outcome["record_installation"] == "COMMITTED"
    assert outcome["reason"] == "INE_CLEANUP_UNCERTAIN" and outcome["cleanup_status"] == "UNCERTAIN"
    assert outcome["ine_evidence_ids"] == allocation["ine_evidence_ids"]
    assert skipped["event"] == "DETAIL_COLLECTION_NOT_ATTEMPTED"
    assert not (tmp_path / "detail-called").exists()
    assert (tmp_path / "ine-calls").read_text().splitlines() == ["hours"]
    observation = show(root, allocation["run_id"])["observation"]
    assert observation["ine_observations"]["hours"]["state"] == "UNKNOWN"
    assert observation["ine_observations"]["holiday"]["state"] == "NOT_ATTEMPTED"
    if mode == "cleanup":
        assert observation["ine_observations"]["hours"]["capture"] == "COMMITTED"
        assert observation["ine_observations"]["hours"]["transport"] == "CHILD_CLEANUP_UNCERTAIN"


def test_cli_v4_navigation_and_detail_with_no_network(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    prior = subprocess.run(
        cli(root, config, supplement, *flags(), "--enable-ine"),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert prior.returncode == 10, (prior.stdout, prior.stderr)
    allocation = json.loads(prior.stdout.splitlines()[0])
    ids = allocation["ine_evidence_ids"]
    assert len({allocation["run_id"], allocation["candidate_id"], *ids.values()}) == 4
    observed = show(root, allocation["run_id"])
    assert observed["observation"]["schema"] == runs._RESULT_SCHEMA_V4
    assert observed["ine_evidence_ids"] == ids
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_detail_collection",
        "run",
        "--calendar-root",
        str(root),
        "--run-id",
        allocation["run_id"],
        "--budget-seconds",
        "20",
        "--per-request-seconds",
        "3",
        "--trusted-local-posix",
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert len((tmp_path / "detail-called").read_text().splitlines()) == 2


@pytest.mark.parametrize(
    "mode",
    ["no_request", "record_no_request", "wrong_id", "empty_id", "ine_unknown", "preview_unknown"],
)
def test_v4_detail_requires_explicit_navigation_and_bound_candidate(tmp_path, monkeypatch, mode):
    root, _ = setup_fake(tmp_path, monkeypatch, 1)
    observed = {
        "intent": "VISIBLE_UNAUTHENTICATED",
        "record": "VISIBLE_UNAUTHENTICATED",
        "navigation_requested": True,
        "candidate_id": CID,
        "observation": {
            "schema": runs._RESULT_SCHEMA_V4,
            "navigation_requested": True,
            "candidate_id": CID,
            "candidate_state": "COMMITTED",
            "preview": runs._report_data(
                replace(report(), candidate_id=CID, candidate_status="COMMITTED"), CID
            ),
            "ine_observations": {
                "hours": {"state": "PARTIAL_OBSERVATION"},
                "holiday": {"state": "FAILED"},
            },
        },
    }
    if mode == "no_request":
        observed["navigation_requested"] = False
    elif mode == "record_no_request":
        observed["observation"]["navigation_requested"] = False
    elif mode == "wrong_id":
        observed["observation"]["candidate_id"] = "f" * 32
    elif mode == "empty_id":
        observed["candidate_id"] = observed["observation"]["candidate_id"] = ""
    elif mode == "ine_unknown":
        observed["observation"]["ine_observations"]["hours"]["state"] = "UNKNOWN"
    else:
        observed["observation"]["preview"]["candidate_reason"] = "CHILD_CLEANUP_UNCERTAIN"
    monkeypatch.setattr(collection.runs, "show_once", lambda **_: observed)
    monkeypatch.setattr(
        collection.inbox, "read_candidate", lambda **_: pytest.fail("candidate read")
    )
    assert invoke(root)["reason"] == "RUN_NOT_ELIGIBLE"
