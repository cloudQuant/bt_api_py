"""Offline supplement capture and observation integration; all data is synthetic."""

import json
import os
import subprocess

import pytest

from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_supplement_capture as capture
from tests.unit.test_trading_calendar_candidate_inbox import input_content
from tests.unit.test_trading_calendar_observation_run import cli, env_without_network, run_args
from tests.unit.test_trading_calendar_preview_catalog import setup_preview

FS = "TRUSTED_LOCAL_POSIX"
RID = "a" * 32
CID = "b" * 32


def test_real_observation_captures_same_guarded_yaml_without_stdout_content(tmp_path):
    import hashlib

    root, _, config, supplement = setup_preview(tmp_path)
    env = env_without_network(tmp_path)
    result = subprocess.run(
        cli(root, config, supplement, "--capture-supplement"),
        capture_output=True,
        text=True,
        timeout=40,
        env=env,
        check=False,
    )
    assert result.returncode == 10, (result.stdout, result.stderr)
    allocation, event = [json.loads(line) for line in result.stdout.splitlines()]
    assert allocation["capture_id"] != allocation["run_id"]
    assert event["capture_state"] == "COMMITTED"
    assert not any(str(p) in result.stdout + result.stderr for p in (root, config, supplement))
    shown = runs.show_once(root=str(root), run_id=allocation["run_id"], filesystem=FS)
    assert shown["intent"] == shown["record"] == "VISIBLE_UNAUTHENTICATED"
    assert shown["observation"]["schema"].endswith("/6")
    assert shown["capture_visibility"] == "VISIBLE_UNAUTHENTICATED"
    assert shown["capture_association"] == "NOT_PROVEN"
    public = capture.read_capture(
        root=str(root), capture_id=allocation["capture_id"], filesystem=FS
    )
    assert "content" not in public and public["payload_sha256"] == event["capture_payload_sha256"]
    full = capture.read_capture(
        root=str(root), capture_id=allocation["capture_id"], filesystem=FS, include_content=True
    )
    from bt_api_ctp.calendar_candidate_normalize import normalize_calendar_candidate
    from bt_api_ctp.calendar_supplement import _validate

    assert full["content"] == json.loads(
        normalize_calendar_candidate(_validate(input_content())).json_bytes
    )
    normalized = normalize_calendar_candidate(_validate(input_content())).json_bytes
    assert full["normalized_content_sha256"] == hashlib.sha256(normalized).hexdigest()
    assert full["normalized_content_sha256"] != hashlib.sha256(supplement.read_bytes()).hexdigest()
    assert not (root / "CURRENT.json").exists()
    assert "content" not in result.stdout


def checked_capture(root, config, supplement):
    from bt_api_py.trading_calendar_check import _check_calendar_inputs_with_capture_candidate

    report, candidate, guard = _check_calendar_inputs_with_capture_candidate(
        config_file=str(config), calendar_root=str(root), supplement_file=str(supplement)
    )
    assert report.exit_code == 0 and candidate is not None and callable(guard)
    return candidate, guard


def retain(root, candidate, guard, capture_id=CID):
    return capture.retain_capture(
        root=str(root),
        run_id=RID,
        capture_id=capture_id,
        candidate=candidate,
        guard=guard,
        filesystem=FS,
    )


def test_guard_detects_source_replacement_before_write(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    replacement = supplement.with_suffix(".new")
    replacement.write_text(supplement.read_text(), encoding="utf-8")
    os.replace(replacement, supplement)
    assert retain(root, candidate, guard)["status"] == "NOT_COMMITTED"
    assert capture.read_capture(root=str(root), capture_id=CID, filesystem=FS)["status"] in {
        "UNKNOWN",
        "NOT_OBSERVED",
    }


def test_guard_detects_source_replacement_after_temporary_fsync(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    original = capture.os.fsync
    calls = 0

    def replaced(fd):
        nonlocal calls
        original(fd)
        calls += 1
        if calls == 1:
            replacement = supplement.with_suffix(".new")
            replacement.write_text(supplement.read_text(), encoding="utf-8")
            os.replace(replacement, supplement)

    monkeypatch.setattr(capture.os, "fsync", replaced)
    assert retain(root, candidate, guard)["status"] == "NOT_COMMITTED"
    assert not (root / "supplement-captures" / f"capture-{CID}.json").exists()


def test_guard_detects_source_replacement_during_link(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    original = capture.os.link

    def replace_then_link(*args, **kwargs):
        if args[1] == f"capture-{CID}.json":
            replacement = supplement.with_suffix(".new")
            replacement.write_bytes(supplement.read_bytes())
            os.replace(replacement, supplement)
        return original(*args, **kwargs)

    monkeypatch.setattr(capture, "_supported", lambda: True)
    monkeypatch.setattr(capture.os, "link", replace_then_link)
    result = retain(root, candidate, guard)
    assert result["status"] == "COMMIT_UNKNOWN" and result["payload_sha256"] is None
    observed = capture.read_capture(root=str(root), capture_id=CID, filesystem=FS)
    assert observed["status"] == "VISIBLE_UNAUTHENTICATED" and observed["run_id"] == RID


def test_candidate_mutation_during_link_is_unknown(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    original = capture.os.link

    def mutate_then_link(*args, **kwargs):
        if args[1] == f"capture-{CID}.json":
            candidate.content["sources"][0]["description"] = "changed"
        return original(*args, **kwargs)

    monkeypatch.setattr(capture, "_supported", lambda: True)
    monkeypatch.setattr(capture.os, "link", mutate_then_link)
    result = retain(root, candidate, guard)
    assert result["status"] == "COMMIT_UNKNOWN" and result["payload_sha256"] is None
    assert capture.read_capture(root=str(root), capture_id=CID, filesystem=FS)["status"] == (
        "VISIBLE_UNAUTHENTICATED"
    )


def test_mutation_before_write_is_not_captured(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    original = capture._normalize
    calls = 0

    def changed(value):
        nonlocal calls
        calls += 1
        result = original(value)
        if calls == 1:
            candidate.content["sources"][0]["description"] = "changed"
        return result

    monkeypatch.setattr(capture, "_normalize", changed)
    assert retain(root, candidate, guard)["status"] == "NOT_COMMITTED"
    assert not (root / "supplement-captures" / f"capture-{CID}.json").exists()


def test_same_id_writers_do_not_overwrite(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retain(root, candidate, guard), range(2)))
    assert sum(r["status"] == "COMMITTED" for r in results) == 1
    assert all(r["status"] in {"COMMITTED", "NOT_COMMITTED", "COMMIT_UNKNOWN"} for r in results)
    original = capture.read_capture(
        root=str(root), capture_id=CID, filesystem=FS, include_content=True
    )
    assert retain(root, candidate, guard)["status"] == "NOT_COMMITTED"
    assert (
        capture.read_capture(root=str(root), capture_id=CID, filesystem=FS, include_content=True)
        == original
    )


def test_lock_conflict_does_not_write(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    fds, chain = capture._open(str(root), True)
    lock, _ = capture.storage._lock(fds[-1], chain)
    try:
        assert retain(root, candidate, guard)["status"] == "NOT_COMMITTED"
        assert not (root / "supplement-captures" / f"capture-{CID}.json").exists()
    finally:
        os.close(lock)
        capture.runs._close(fds)


def test_unknown_after_link_is_checked_with_original_id(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    original = capture._remove_owned_temp

    def after_commit(*args):
        original(*args)
        raise OSError("synthetic uncertainty")

    monkeypatch.setattr(capture, "_remove_owned_temp", after_commit)
    result = retain(root, candidate, guard)
    assert result["status"] == "COMMIT_UNKNOWN" and result["payload_sha256"] is None
    assert (
        capture.read_capture(root=str(root), capture_id=CID, filesystem=FS)["status"]
        == "VISIBLE_UNAUTHENTICATED"
    )


def test_tampered_normalized_content_is_unknown(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    assert retain(root, candidate, guard)["status"] == "COMMITTED"
    path = root / "supplement-captures" / f"capture-{CID}.json"
    doc = json.loads(path.read_bytes())
    doc["body"]["content"]["sources"][0]["description"] = "tampered"
    path.write_bytes(capture.storage._envelope(doc["body"]))
    assert (
        capture.read_capture(root=str(root), capture_id=CID, filesystem=FS)["status"] == "UNKNOWN"
    )


def test_unknown_worker_status_is_never_upgraded_by_visible_capture(tmp_path, monkeypatch):
    from bt_api_py.trading_calendar_preview import CapturePreviewResult
    from tests.unit.test_trading_calendar_preview_reports import report

    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    assert retain(root, candidate, guard)["status"] == "COMMITTED"
    monkeypatch.setattr(
        runs, "_supervise", lambda _: CapturePreviewResult(report(), CID, "COMMIT_UNKNOWN", None)
    )
    result = runs.run_once(**run_args(root, config, supplement), capture_id=CID)
    assert result["capture_state"] == "COMMIT_UNKNOWN"
    shown = runs.show_once(root=str(root), run_id=RID, filesystem=FS)
    assert shown["observation"]["capture_state"] == "COMMIT_UNKNOWN"
    assert shown["capture_visibility"] == "VISIBLE_UNAUTHENTICATED"
    assert shown["capture_association"] == "NOT_PROVEN"


def test_input_guard_reuses_single_ctp_reader_result(tmp_path, monkeypatch):
    from bt_api_ctp import calendar_supplement_reader as reader

    from bt_api_py import trading_calendar_check as check

    root, _, config, supplement = setup_preview(tmp_path)
    calls = []
    original = reader.read_supplement

    def read(path):
        calls.append(True)
        return original(path)

    monkeypatch.setattr(reader, "read_supplement", read)
    report, candidate, guard = check._check_calendar_inputs_with_capture_candidate(
        config_file=str(config), calendar_root=str(root), supplement_file=str(supplement)
    )
    assert report.exit_code == 0 and len(calls) == 1
    assert retain(root, candidate, guard)["status"] == "COMMITTED"
    assert len(calls) == 1


def test_capture_and_preview_candidate_are_independent(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = env_without_network(tmp_path)
    result = subprocess.run(
        cli(root, config, supplement, "--capture-supplement", "--retain-candidate"),
        capture_output=True,
        text=True,
        timeout=40,
        env=env,
        check=False,
    )
    assert result.returncode == 10, (result.stdout, result.stderr)
    allocation, event = [json.loads(line) for line in result.stdout.splitlines()]
    assert len({allocation["run_id"], allocation["candidate_id"], allocation["capture_id"]}) == 3
    assert event["candidate_state"] == event["capture_state"] == "COMMITTED"
    observed = runs.show_once(root=str(root), run_id=allocation["run_id"], filesystem=FS)
    assert observed["record"] == "VISIBLE_UNAUTHENTICATED"
    assert (
        observed["candidate_visibility"]
        == observed["capture_visibility"]
        == "VISIBLE_UNAUTHENTICATED"
    )


def test_invalid_input_records_not_attempted_capture_without_retry(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    supplement.write_text("not: [valid", encoding="utf-8")
    env = env_without_network(tmp_path)
    result = subprocess.run(
        cli(root, config, supplement, "--capture-supplement"),
        capture_output=True,
        text=True,
        timeout=40,
        env=env,
        check=False,
    )
    assert result.returncode == 24, (result.stdout, result.stderr)
    allocation, event = [json.loads(line) for line in result.stdout.splitlines()]
    assert event["capture_state"] == "NOT_ATTEMPTED"
    shown = runs.show_once(root=str(root), run_id=allocation["run_id"], filesystem=FS)
    assert shown["record"] == "VISIBLE_UNAUTHENTICATED"
    assert shown["observation"]["capture_state"] == "NOT_ATTEMPTED"
    assert not (root / "supplement-captures" / f"capture-{allocation['capture_id']}.json").exists()


def test_legacy_intents_and_results_stay_readable(tmp_path, monkeypatch):
    from tests.unit.test_trading_calendar_preview_reports import report

    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda _: report())
    result = runs.run_once(**run_args(root, config, supplement))
    assert result["record_installation"] == "COMMITTED"
    shown = runs.show_once(root=str(root), run_id=RID, filesystem=FS)
    assert shown["observation"]["schema"] == "bt_api.calendar-observation-run/1"
    assert "capture_id" not in shown


def test_capture_precedes_merge_and_keeps_unmerged_content(tmp_path):
    from bt_api_py.trading_calendar_candidate_inbox import CandidateRetention
    from tests.unit.test_trading_calendar_preview import checked, run, source

    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    order = []

    def capture_writer(value, captured_guard):
        order.append("capture")
        assert value is candidate and captured_guard is guard
        return retain(root, value, captured_guard)["status"]

    def candidate_writer(**kwargs):
        order.append("merged")
        assert kwargs["merged"] is not None
        assert kwargs["merged"].supplement.content != candidate.content
        return CandidateRetention("COMMITTED", "NONE", "c" * 32)

    report = run(
        enable_shfe=True,
        collector=lambda **_: source("HTTP_OK"),
        checker=lambda **_: (checked(), candidate, guard),
        candidate_id="c" * 32,
        candidate_writer=candidate_writer,
        supplement_capture_writer=capture_writer,
    )
    assert report.candidate_status == "COMMITTED" and order == ["capture", "merged"]
    observed = capture.read_capture(
        root=str(root), capture_id=CID, filesystem=FS, include_content=True
    )
    from bt_api_ctp.calendar_candidate_normalize import normalize_calendar_candidate

    assert observed["content"] == json.loads(normalize_calendar_candidate(candidate).json_bytes)


def test_preview_interrupt_keeps_capture_unknown_and_never_retries(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    calls = []

    def interrupted(_):
        calls.append(True)
        raise KeyboardInterrupt

    monkeypatch.setattr(runs, "_supervise", interrupted)
    result = runs.run_once(**run_args(root, config, supplement), capture_id=CID)
    assert calls == [True] and result["capture_state"] == "COMMIT_UNKNOWN"
    assert result["record_installation"] == "NOT_ATTEMPTED"
    shown = runs.show_once(root=str(root), run_id=RID, filesystem=FS)
    assert shown["intent"] == "VISIBLE_UNAUTHENTICATED" and shown["record"] == "NOT_OBSERVED"
    assert shown["capture_id"] == CID and shown["capture_association"] == "NOT_PROVEN"


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_parent_capture_read_failure_never_claims_commit(tmp_path, monkeypatch, error):
    from bt_api_py.trading_calendar_preview import CapturePreviewResult
    from tests.unit.test_trading_calendar_preview_reports import report

    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(
        runs, "_supervise", lambda _: CapturePreviewResult(report(), CID, "COMMITTED", "c" * 64)
    )

    def failed_read(**kwargs):
        raise error("synthetic readback failure")

    monkeypatch.setattr(capture, "read_capture", failed_read)
    result = runs.run_once(**run_args(root, config, supplement), capture_id=CID)
    assert result["capture_state"] == "COMMIT_UNKNOWN"
    assert result["capture_payload_sha256"] is None and result["capture_outcome"] == "UNKNOWN"
    if error is KeyboardInterrupt:
        assert result["record_installation"] == "NOT_ATTEMPTED"
        assert result["reason"] == "PREVIEW_INTERRUPTED_CLEANUP_UNKNOWN"
    else:
        assert result["reason"] == "CAPTURE_OUTCOME_UNKNOWN"
    assert not (root / "supplement-captures" / f"capture-{CID}.json").exists()


def test_cleanup_uncertain_cannot_claim_capture_commit(tmp_path, monkeypatch):
    from dataclasses import replace

    from bt_api_py.trading_calendar_preview import CapturePreviewResult
    from tests.unit.test_trading_calendar_preview_reports import report

    root, _, config, supplement = setup_preview(tmp_path)
    candidate, guard = checked_capture(root, config, supplement)
    committed = retain(root, candidate, guard)
    assert committed["status"] == "COMMITTED"
    uncertain = replace(report(), status="ERROR", reason="WORKER_CLEANUP_UNCERTAIN", exit_code=23)
    monkeypatch.setattr(
        runs,
        "_supervise",
        lambda _: CapturePreviewResult(uncertain, CID, "COMMITTED", committed["payload_sha256"]),
    )
    result = runs.run_once(**run_args(root, config, supplement), capture_id=CID)
    assert result["capture_state"] == "COMMIT_UNKNOWN" and result["capture_payload_sha256"] is None


def test_capture_failure_prevents_ine_followup(tmp_path, monkeypatch):
    from bt_api_py.trading_calendar_preview import CapturePreviewResult, PreviewReport

    root, _, config, supplement = setup_preview(tmp_path)
    report = PreviewReport(
        "ERROR",
        "INPUT_CHECK_FAILED",
        20,
        input_status="CHECKED",
        input_code="SUPPLEMENT_UNVERIFIED",
        supplement_status="CANDIDATE",
    )
    monkeypatch.setattr(
        runs, "_supervise", lambda _: CapturePreviewResult(report, CID, "NOT_COMMITTED", None)
    )
    monkeypatch.setattr(
        runs,
        "_ine_observations",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected network")),
    )
    result = runs.run_once(
        **run_args(root, config, supplement),
        capture_id=CID,
        ine_evidence_ids={"hours": "c" * 32, "holiday": "d" * 32},
    )
    assert (
        result["capture_state"] == "NOT_COMMITTED" and result["record_installation"] == "COMMITTED"
    )
    shown = runs.show_once(root=str(root), run_id=RID, filesystem=FS)
    assert shown["record"] == "VISIBLE_UNAUTHENTICATED"
    assert all(
        item["state"] == "NOT_ATTEMPTED"
        for item in shown["observation"]["ine_observations"].values()
    )


def _capture_process(root, config, supplement, queue):
    candidate, guard = checked_capture(root, config, supplement)
    queue.put(retain(root, candidate, guard)["status"])


def test_process_writers_do_not_replace_same_capture_id(tmp_path):
    import multiprocessing

    root, _, config, supplement = setup_preview(tmp_path)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [
        context.Process(target=_capture_process, args=(root, config, supplement, queue))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    statuses = [queue.get(timeout=2) for _ in processes]
    assert statuses.count("COMMITTED") == 1
    assert set(statuses) <= {"COMMITTED", "NOT_COMMITTED", "COMMIT_UNKNOWN"}
    assert (
        capture.read_capture(root=str(root), capture_id=CID, filesystem=FS)["status"]
        == "VISIBLE_UNAUTHENTICATED"
    )


def test_capture_run_binding_rechecked_without_promoting_observation(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    result = subprocess.run(
        cli(root, config, supplement, "--capture-supplement"),
        capture_output=True,
        text=True,
        timeout=40,
        env=env_without_network(tmp_path),
        check=False,
    )
    assert result.returncode == 10
    allocation = json.loads(result.stdout.splitlines()[0])
    path = root / "supplement-captures" / f"capture-{allocation['capture_id']}.json"
    document = json.loads(path.read_bytes())
    document["body"]["run_id"] = "f" * 32
    path.write_bytes(capture.storage._envelope(document["body"]))
    shown = runs.show_once(root=str(root), run_id=allocation["run_id"], filesystem=FS)
    assert shown["record"] == "VISIBLE_UNAUTHENTICATED"
    assert shown["capture_visibility"] == "UNKNOWN"
    assert shown["capture_association"] == "NOT_PROVEN"


def test_capture_failure_stops_explicit_ine_retries_without_legacy_schema_change(
    tmp_path, monkeypatch
):
    from bt_api_py.trading_calendar_preview import CapturePreviewResult, PreviewReport

    root, _, config, supplement = setup_preview(tmp_path)
    report = PreviewReport(
        "ERROR",
        "INPUT_CHECK_FAILED",
        20,
        input_status="CHECKED",
        input_code="SUPPLEMENT_UNVERIFIED",
        supplement_status="CANDIDATE",
    )
    monkeypatch.setattr(
        runs, "_supervise", lambda _: CapturePreviewResult(report, CID, "NOT_COMMITTED", None)
    )
    monkeypatch.setattr(
        runs,
        "_ine_retry_observations",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected network")),
    )
    ids = {"hours": "c" * 32, "holiday": "d" * 32}
    retry = {
        "evidence_ids": {"hours": [ids["hours"]], "holiday": [ids["holiday"]]},
        "budget_seconds": 10.0,
        "interval_seconds": 1.0,
    }
    result = runs.run_once(
        **run_args(root, config, supplement),
        capture_id=CID,
        ine_evidence_ids=ids,
        ine_retry_config=retry,
    )
    assert (
        result["capture_state"] == "NOT_COMMITTED" and result["record_installation"] == "COMMITTED"
    )
    shown = runs.show_once(root=str(root), run_id=RID, filesystem=FS)
    assert shown["record"] == "VISIBLE_UNAUTHENTICATED"
    assert shown["observation"]["ine_retry_result"]["stop_reason"] == "CAPTURE_NOT_COMMITTED"
    assert shown["observation"]["capture_state"] == "NOT_COMMITTED"


def test_worker_protocol_rejects_duplicate_or_malformed_capture_events():
    from bt_api_py.trading_calendar_preview import _capture_worker_event

    good = {
        "schema": "bt_api.calendar-supplement-capture-event/1",
        "event": "SUPPLEMENT_CAPTURE_RESULT",
        "capture_id": CID,
        "status": "COMMITTED",
        "payload_sha256": "c" * 64,
    }
    line = json.dumps(good).encode()
    assert _capture_worker_event([line], CID) == ("COMMITTED", "c" * 64)
    assert _capture_worker_event([line, line], CID) == ("COMMIT_UNKNOWN", None)
    duplicate = line[:-1] + b',"capture_id":"' + CID.encode() + b'"}'
    assert _capture_worker_event([duplicate], CID) == ("COMMIT_UNKNOWN", None)
    good["unexpected"] = "private"
    assert _capture_worker_event([json.dumps(good).encode()], CID) == ("COMMIT_UNKNOWN", None)


def test_candidate_committed_does_not_upgrade_unknown_capture(tmp_path, monkeypatch):
    from dataclasses import replace

    from bt_api_py.trading_calendar_preview import CapturePreviewResult
    from tests.unit.test_trading_calendar_preview_reports import report

    root, _, config, supplement = setup_preview(tmp_path)
    candidate_id = "c" * 32
    preview = replace(report(), candidate_id=candidate_id, candidate_status="COMMITTED")
    monkeypatch.setattr(
        runs, "_supervise", lambda _: CapturePreviewResult(preview, CID, "COMMIT_UNKNOWN", None)
    )
    result = runs.run_once(
        **run_args(root, config, supplement), capture_id=CID, candidate_id=candidate_id
    )
    assert result["candidate_state"] == "COMMITTED"
    assert result["capture_state"] == "COMMIT_UNKNOWN" and result["capture_outcome"] == "UNKNOWN"
    assert result["reason"] == "CAPTURE_OUTCOME_UNKNOWN"
    shown = runs.show_once(root=str(root), run_id=RID, filesystem=FS)
    assert shown["record"] == "VISIBLE_UNAUTHENTICATED"
    assert shown["observation"]["capture_state"] == "COMMIT_UNKNOWN"


def test_unknown_capture_blocks_followup_detail_collection(monkeypatch, capsys, tmp_path):
    def fake_run(**kwargs):
        return {
            "run_id": kwargs["run_id"],
            "candidate_id": kwargs["candidate_id"],
            "intent_installation": "COMMITTED",
            "record_installation": "COMMITTED",
            "candidate_state": "COMMITTED",
            "capture_state": "COMMIT_UNKNOWN",
            "reason": "NONE",
        }

    monkeypatch.setattr(runs, "run_once", fake_run)
    monkeypatch.setattr(
        runs,
        "_detail_collection_run",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    rc = runs.main(
        [
            "run",
            "--calendar-root",
            str(tmp_path),
            "--config",
            "/fake/config",
            "--supplement",
            "/fake/supplement",
            "--budget-seconds",
            "5",
            "--trusted-local-posix",
            "--capture-supplement",
            "--retain-candidate",
            "--fetch-product-navigation",
            "--collect-product-details",
            "--detail-budget-seconds",
            "1",
        ]
    )
    assert rc == 25
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[-1]["event"] == "DETAIL_COLLECTION_NOT_ATTEMPTED"
    assert lines[-1]["reason"] == "CAPTURE_OUTCOME_UNKNOWN"
