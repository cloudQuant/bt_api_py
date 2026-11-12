"""Offline evidence retention and association; no network or account."""

import builtins
import hashlib
import json
import multiprocessing
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from bt_api_ctp import calendar_shfe_evidence_fetch as ef
from bt_api_ctp import calendar_shfe_fetch as shfe

from bt_api_py import trading_calendar_source_evidence as source
from tests.unit.test_trading_calendar_candidate_inbox import HOURS, holiday_html, retain

ROOT_MODE = source.TRUSTED_LOCAL_POSIX
EID = "b" * 32
CID = "a" * 32


def fake_runner(raw, calls):
    def run(kind, *_args):
        calls.append(kind)
        url = shfe._SOURCE if kind == "hours" else shfe._HOLIDAY_SOURCE
        parser = shfe.parse_shfe_page if kind == "hours" else shfe.parse_shfe_holiday
        page = parser(source_id=url, html=raw)
        return "HTTP_OK", ef.PublicPageEvidence(
            kind, url, hashlib.sha256(raw).hexdigest(), raw, page
        )

    return run


def capture(root, kind, runner, evidence_id=EID):
    return source.capture(
        root=str(root),
        evidence_id=evidence_id,
        kind=kind,
        filesystem=ROOT_MODE,
        emit=lambda _: None,
        runner=runner,
    )


def show(root, evidence_id=EID, include=False):
    return source.show(
        root=str(root), evidence_id=evidence_id, filesystem=ROOT_MODE, include_public_bytes=include
    )


@pytest.mark.parametrize("kind,raw", [("hours", HOURS), ("holiday", holiday_html())])
def test_capture_show_and_match_prior_candidate(tmp_path, kind, raw):
    assert retain(tmp_path).status == "COMMITTED"
    calls = []
    current = tmp_path / "CURRENT.json"
    current.write_text("unchanged")
    assert capture(tmp_path, kind, fake_runner(raw, calls))["status"] == "COMMITTED"
    assert calls == [kind]
    observed = show(tmp_path, include=True)
    assert observed["status"] == "VISIBLE_UNAUTHENTICATED" and observed["raw_bytes"] == raw
    assert observed["utf8_text"].encode() == raw
    assert observed["source_sha256"] == hashlib.sha256(raw).hexdigest()
    expected_prefix = "shfe.hours." if kind == "hours" else "shfe.holiday.2026."
    assert observed["candidate_source_id"] == expected_prefix + observed["source_sha256"]
    assert (
        observed["source_authentication"] == "NOT_PROVEN"
        and observed["publication"] == "NOT_AUTHORIZED"
    )
    match = source.match_candidate(
        root=str(tmp_path), evidence_id=EID, candidate_id=CID, filesystem=ROOT_MODE
    )
    assert match["status"] == "CANDIDATE_DECLARED_DIGEST_MATCH"
    assert match["observation_order"] == "NOT_PROVEN"
    assert match["old_source_bytes"] == "NOT_AVAILABLE"
    assert capture(tmp_path, kind, fake_runner(raw, calls))["status"] == "NOT_COMMITTED"
    assert calls == [kind] and current.read_text() == "unchanged"


def test_different_bytes_do_not_match_candidate(tmp_path):
    assert retain(tmp_path).status == "COMMITTED"
    assert capture(tmp_path, "hours", fake_runner(HOURS + b" ", []))["status"] == "COMMITTED"
    assert (
        source.match_candidate(
            root=str(tmp_path), evidence_id=EID, candidate_id=CID, filesystem=ROOT_MODE
        )["status"]
        == "UNKNOWN"
    )


def test_candidate_claim_can_falsely_declare_observed_digest(tmp_path):
    different_html = HOURS.replace("合成甲".encode(), "合成乙".encode())
    assert different_html != HOURS
    page = shfe.parse_shfe_page(source_id=shfe._SOURCE, html=different_html)
    forged = replace(page, sha256=hashlib.sha256(HOURS).hexdigest())
    assert retain(tmp_path, hours=forged).status == "COMMITTED"
    assert capture(tmp_path, "hours", fake_runner(HOURS, []))["status"] == "COMMITTED"
    result = source.match_candidate(
        root=str(tmp_path), evidence_id=EID, candidate_id=CID, filesystem=ROOT_MODE
    )
    assert result["status"] == "CANDIDATE_DECLARED_DIGEST_MATCH"
    assert result["old_source_bytes"] == "NOT_AVAILABLE"
    assert result["observation_order"] == "NOT_PROVEN"
    assert result["source_authentication"] == "NOT_PROVEN"
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_source_evidence",
        "match",
        "--calendar-root",
        str(tmp_path),
        "--evidence-id",
        EID,
        "--candidate-id",
        CID,
        "--trusted-local-posix",
    ]
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    cli_result = json.loads(run.stdout)
    assert run.returncode == 10 and cli_result["status"] == "CANDIDATE_DECLARED_DIGEST_MATCH"
    assert cli_result["old_source_bytes"] == "NOT_AVAILABLE"


def test_result_not_committed_does_not_claim_no_attempt(tmp_path, monkeypatch):
    original = source.storage._write
    calls = []

    def write(fd, chain, name, data, limit):
        if name.startswith("result-"):
            return "NOT_COMMITTED"
        return original(fd, chain, name, data, limit)

    monkeypatch.setattr(source.storage, "_write", write)
    result = capture(tmp_path, "hours", fake_runner(HOURS, calls))
    assert result == {
        "status": "ATTEMPT_UNKNOWN",
        "reason": "RESULT_NOT_COMMITTED",
        "evidence_id": EID,
    }
    assert show(tmp_path)["reason"] == "RESULT_MISSING"
    assert capture(tmp_path, "hours", fake_runner(HOURS, calls))["status"] == "NOT_COMMITTED"
    assert calls == ["hours"]


@pytest.mark.parametrize("raw,expected", [(b"\xef\xbb\xbf" + HOURS, True), (b"\xff\xfe", False)])
def test_utf8_bom_or_non_utf8_is_stored_exactly(tmp_path, raw, expected):
    assert capture(tmp_path, "hours", fake_runner(raw, []))["status"] == "COMMITTED"
    result = show(tmp_path, include=True)
    assert result["raw_bytes"] == raw
    assert (result["utf8_text"] is not None) is expected
    assert result["page_status"] == ("NEEDS_REVIEW" if expected else "INVALID_UTF8")


def test_cleanup_uncertain_and_missing_result_do_not_retry(tmp_path):
    calls = []

    def interrupted(*args):
        calls.append(args)
        raise KeyboardInterrupt

    assert capture(tmp_path, "hours", interrupted)["status"] == "ATTEMPT_UNKNOWN"
    assert show(tmp_path)["reason"] == "RESULT_MISSING"
    assert capture(tmp_path, "hours", interrupted)["status"] == "NOT_COMMITTED"
    assert len(calls) == 1
    eid = "c" * 32
    result = capture(tmp_path, "hours", lambda *args: ("CHILD_CLEANUP_UNCERTAIN", None), eid)
    assert result["transport"] == "CHILD_CLEANUP_UNCERTAIN"
    assert show(tmp_path, eid)["transport"] == "CHILD_CLEANUP_UNCERTAIN"


def test_result_uncertain_uses_original_id_without_second_request(tmp_path, monkeypatch):
    original = source.storage._write
    calls = []

    def write(fd, chain, name, data, limit):
        result = original(fd, chain, name, data, limit)
        return "COMMIT_UNKNOWN" if name.startswith("result-") else result

    monkeypatch.setattr(source.storage, "_write", write)
    result = capture(tmp_path, "hours", fake_runner(HOURS, calls))
    assert result["status"] == "COMMIT_UNKNOWN"
    assert show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"
    assert capture(tmp_path, "hours", fake_runner(HOURS, calls))["status"] == "NOT_COMMITTED"
    assert len(calls) == 1


@pytest.mark.parametrize("missing", ["calendar_shfe_evidence_fetch", "calendar_shfe_fetch"])
def test_missing_provider_dependency_is_detected_before_claim(tmp_path, monkeypatch, missing):
    original = builtins.__import__

    def import_module(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "bt_api_ctp" and missing in fromlist:
            raise ModuleNotFoundError("synthetic missing dependency")
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_module)
    calls = []
    result = capture(tmp_path, "hours", fake_runner(HOURS, calls))
    assert result["status"] == "NOT_COMMITTED" and result["evidence_id"] == EID
    assert not (tmp_path / "source-evidence").exists() and calls == []


def test_exception_after_claim_preserves_attempt_unknown_in_api_and_cli(
    tmp_path, monkeypatch, capsys
):
    def fail(**_kwargs):
        raise ImportError("synthetic dependency loss")

    monkeypatch.setattr(ef, "collect_shfe_evidence", fail)
    result = capture(tmp_path, "hours", fake_runner(HOURS, []))
    assert result["status"] == "ATTEMPT_UNKNOWN" and result["evidence_id"] == EID
    assert show(tmp_path)["reason"] == "RESULT_MISSING"
    other_id = "c" * 32
    exit_code = source.main(
        [
            "capture",
            "--calendar-root",
            str(tmp_path),
            "--evidence-id",
            other_id,
            "--kind",
            "hours",
            "--trusted-local-posix",
        ]
    )
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert exit_code == 25
    assert output[-1]["status"] == "ATTEMPT_UNKNOWN" and output[-1]["evidence_id"] == other_id
    assert show(tmp_path, other_id)["reason"] == "RESULT_MISSING"


def test_interruption_during_post_claim_directory_open_is_attempt_unknown(tmp_path, monkeypatch):
    original = source._open

    def open_directory(root, create):
        if not create:
            raise KeyboardInterrupt
        return original(root, create)

    monkeypatch.setattr(source, "_open", open_directory)
    result = capture(tmp_path, "hours", fake_runner(HOURS, []))
    assert result["status"] == "ATTEMPT_UNKNOWN" and result["evidence_id"] == EID
    monkeypatch.setattr(source, "_open", original)
    assert show(tmp_path)["reason"] == "RESULT_MISSING"


def test_interruption_after_claim_during_cleanup_keeps_original_id(tmp_path, monkeypatch):
    original_close = source.runs._close
    raised = []

    def close_then_interrupt(descriptors):
        original_close(descriptors)
        if not raised:
            raised.append(True)
            raise KeyboardInterrupt

    monkeypatch.setattr(source.runs, "_close", close_then_interrupt)
    calls = []
    result = capture(tmp_path, "hours", fake_runner(HOURS, calls))
    assert result["status"] == "ATTEMPT_UNKNOWN" and result["evidence_id"] == EID
    assert calls == [] and show(tmp_path)["reason"] == "RESULT_MISSING"


def test_two_writers_share_claim_and_never_overwrite(tmp_path):
    calls = []
    runner = fake_runner(HOURS, calls)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: capture(tmp_path, "hours", runner), range(2)))
    assert sum(result["status"] == "COMMITTED" for result in results) == 1
    assert len(calls) == 1 and show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"


def _process_capture(root, ready, start, output):
    calls = []
    ready.put(True)
    start.wait(10)
    result = capture(root, "hours", fake_runner(HOURS, calls))
    output.put((result["status"], len(calls)))


def test_two_processes_do_not_reissue_the_same_claim(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, output, start = context.Queue(), context.Queue(), context.Event()
    workers = [
        context.Process(target=_process_capture, args=(tmp_path, ready, start, output))
        for _ in range(2)
    ]
    try:
        for worker in workers:
            worker.start()
        assert ready.get(timeout=15) and ready.get(timeout=15)
        start.set()
        results = [output.get(timeout=15), output.get(timeout=15)]
        assert sum(status == "COMMITTED" for status, _ in results) == 1
        assert sum(calls for _, calls in results) == 1
        assert show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"
    finally:
        for worker in workers:
            worker.join(timeout=15)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)


def test_tampered_result_unknown_and_cli_show_does_not_print_public_bytes(tmp_path):
    assert capture(tmp_path, "hours", fake_runner(HOURS, []))["status"] == "COMMITTED"
    command = [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_source_evidence",
        "show",
        "--calendar-root",
        str(tmp_path),
        "--evidence-id",
        EID,
        "--trusted-local-posix",
    ]
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    assert run.returncode == 10 and json.loads(run.stdout)["status"] == "VISIBLE_UNAUTHENTICATED"
    assert "<table>" not in run.stdout and "合成甲" not in run.stdout
    path = tmp_path / "source-evidence" / f"result-{EID}.json"
    data = json.loads(path.read_bytes())
    data["body"]["ipc"]["raw_base64"] = "invalid"
    path.write_text(json.dumps(data))
    assert show(tmp_path)["status"] == "UNKNOWN"
