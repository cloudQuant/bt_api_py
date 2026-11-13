"""Offline only; the public AKShare network is never invoked."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import date

import pytest
from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract

from bt_api_py import trading_calendar_akshare_observation as obs

OID = "a" * 32
RID = "b" * 32
ROOT_MODE = obs.TRUSTED_LOCAL_POSIX


def provider(rows=None, status="OK", transport="CHILD_OK", calls=None):
    def call(**kwargs):
        if calls is not None:
            calls.append(1)
        return collect_akshare_contract(
            **kwargs, _runner=lambda *_: (transport, {"status": status, "rows": rows or []})
        )

    return call


def observe(root, collector, observation_id=OID, run_id=RID):
    return obs.observe(
        root=str(root),
        observation_id=observation_id,
        run_id=run_id,
        venue="SHFE",
        contract="RB2701",
        requested_date=date(2026, 11, 13),
        timeout=1,
        filesystem=ROOT_MODE,
        _collector=collector,
    )


def show(root, observation_id=OID, run_id=RID, include_content=False):
    return obs.show(
        root=str(root),
        observation_id=observation_id,
        run_id=run_id,
        filesystem=ROOT_MODE,
        include_content=include_content,
    )


def test_success_private_content_and_restart(tmp_path, capsys):
    current = tmp_path / "CURRENT.json"
    current.write_text("unchanged")
    raw = "日盘 09:00-10:15 未知 secret=credential /private/path"
    result = observe(tmp_path, provider([["交易时间", raw], ["名称", "螺纹"]]))
    assert result["status"] == "COMMITTED"
    summary = show(tmp_path)
    assert summary["status"] == "VISIBLE_UNAUTHENTICATED"
    assert summary["observation_status"] == "SUCCESS"
    assert summary["source_type"] == "AKSHARE" and summary["coverage"] == "UNKNOWN"
    assert summary["publication"] == "NOT_AUTHORIZED" and summary["time_role"] == "CANDIDATE"
    assert "secret" not in repr(summary) and "/private/path" not in repr(summary)
    assert capsys.readouterr() == ("", "")
    full = show(tmp_path, include_content=True)
    assert full["content"]["fields"][0]["raw_value"] == raw
    assert full["content"]["time"]["status"] == "NEEDS_REVIEW"
    assert full["content"]["time"]["ranges"][0]["start"] == "09:00"
    assert current.read_text() == "unchanged"
    assert (tmp_path / "akshare-observations").stat().st_mode & 0o077 == 0
    assert all(p.stat().st_mode & 0o077 == 0 for p in (tmp_path / "akshare-observations").iterdir())


@pytest.mark.parametrize(
    ("rows", "child_status", "transport", "expected"),
    [
        ([], "OK", "CHILD_OK", "EMPTY"),
        ([["名称", "x"]], "OK", "CHILD_OK", "MISSING_TRADING_TIME"),
        ([], "DEPENDENCY_UNAVAILABLE", "CHILD_OK", "DEPENDENCY_UNAVAILABLE"),
        ([], "VERSION_MISMATCH", "CHILD_OK", "VERSION_MISMATCH"),
        ([], "FETCH_ERROR", "CHILD_OK", "FETCH_ERROR"),
        ([], "OK", "CHILD_TIMEOUT", "FETCH_TIMEOUT"),
        ([], "OK", "CHILD_CLEANUP_UNCERTAIN", "UNKNOWN"),
    ],
)
def test_result_states(tmp_path, rows, child_status, transport, expected):
    write = observe(tmp_path, provider(rows, child_status, transport))
    assert write["status"] == write["record_state"] == "COMMITTED"
    assert write["observation_status"] == expected and write["requires_readback"]
    result = show(tmp_path)
    assert result["observation_status"] == expected
    assert (result["requested_venue"], result["requested_contract"], result["requested_date"]) == (
        "SHFE",
        "RB2701",
        "2026-11-13",
    )
    assert result["request_scope"] == "DECLARED_NOT_RESPONSE_IDENTITY_OR_TRADING_DAY"
    assert result["descendant_cleanup"] == "NOT_PROVEN"
    if expected in {"FETCH_TIMEOUT", "UNKNOWN"}:
        assert result["timeout_retry"] == "NOT_PROVEN_SAFE"
    if expected in {
        "DEPENDENCY_UNAVAILABLE",
        "VERSION_MISMATCH",
        "FETCH_ERROR",
        "FETCH_TIMEOUT",
        "UNKNOWN",
    }:
        assert result["observed_version"] is None


def test_duplicate_other_field_is_diagnostic_not_candidate(tmp_path):
    rows = [["交易时间", "日盘 09:00-10:00"], ["name", "a"], ["name", "b"]]
    assert observe(tmp_path, provider(rows))["status"] == "COMMITTED"
    read = show(tmp_path, include_content=True)
    assert read["observation_status"] == "STRUCTURE_CHANGED"
    assert read["time_role"] == "DIAGNOSTIC"
    assert read["content"]["time"]["status"] == "SYNTAX_CANDIDATE"


def test_status_split_refused(tmp_path):
    def bad(**kwargs):
        result = provider([["交易时间", "日盘 09:00-10:00"]])(**kwargs)
        return replace(result, status="FETCH_ERROR")

    assert observe(tmp_path, bad)["status"] == "ATTEMPT_UNKNOWN"
    assert show(tmp_path)["status"] == "ATTEMPT_UNKNOWN"


def test_exception_and_duplicate_run_never_retry(tmp_path):
    calls = []

    def broken(**_):
        calls.append(1)
        raise RuntimeError("/private/credential=secret")

    result = observe(tmp_path, broken)
    assert result["status"] == "ATTEMPT_UNKNOWN"
    assert "secret" not in repr(result) and "/private" not in repr(result)
    assert show(tmp_path)["status"] == "ATTEMPT_UNKNOWN"
    assert observe(tmp_path, broken, run_id="c" * 32)["status"] == "NOT_COMMITTED"
    assert len(calls) == 1
    assert show(tmp_path, run_id="c" * 32)["status"] == "UNKNOWN"


def test_write_after_commit_unknown_checked_by_same_id(tmp_path, monkeypatch):
    real = obs.storage._write

    def uncertain(*args):
        result = real(*args)
        return (
            "COMMIT_UNKNOWN" if args[2].startswith("result-") and result == "COMMITTED" else result
        )

    monkeypatch.setattr(obs.storage, "_write", uncertain)
    calls = []
    assert (
        observe(tmp_path, provider([["交易时间", "日盘 09:00-10:00"]], calls=calls))["status"]
        == "COMMIT_UNKNOWN"
    )
    assert show(tmp_path)["status"] == "VISIBLE_UNAUTHENTICATED"
    assert observe(tmp_path, provider(calls=calls))["status"] == "NOT_COMMITTED"
    assert len(calls) == 1


def test_claim_write_unknown_does_not_start_or_retry(tmp_path, monkeypatch):
    real = obs.storage._write

    def uncertain(*args):
        result = real(*args)
        return (
            "COMMIT_UNKNOWN" if args[2].startswith("intent-") and result == "COMMITTED" else result
        )

    monkeypatch.setattr(obs.storage, "_write", uncertain)
    calls = []
    assert observe(tmp_path, provider(calls=calls))["status"] == "COMMIT_UNKNOWN"
    assert show(tmp_path)["status"] == "ATTEMPT_UNKNOWN"
    assert observe(tmp_path, provider(calls=calls))["status"] == "NOT_COMMITTED"
    assert not calls


def test_tamper_digest_and_source_state_are_rejected(tmp_path):
    assert observe(tmp_path, provider([["交易时间", "日盘 09:00-10:00"]]))["status"] == "COMMITTED"
    path = tmp_path / "akshare-observations" / f"result-{OID}.json"
    document = json.loads(path.read_text())
    document["body"]["content"]["fields"][0]["raw_value"] = "day changed"
    path.write_text(json.dumps(document))
    assert show(tmp_path)["status"] == "UNKNOWN"
    path.write_bytes(obs.storage._envelope(document["body"]))
    assert show(tmp_path)["status"] == "UNKNOWN"
    document["body"]["content"]["fields"][0]["raw_value"] = "日盘 09:00-10:00"
    document["body"]["source_type"] = "MOCK"
    path.write_bytes(obs.storage._envelope(document["body"]))
    assert show(tmp_path)["status"] == "UNKNOWN"


def test_claim_is_visible_before_collector_starts(tmp_path):
    def collect(**kwargs):
        assert show(tmp_path)["status"] == "ATTEMPT_UNKNOWN"
        return provider([["交易时间", "日盘 09:00-10:00"]])(**kwargs)

    assert observe(tmp_path, collect)["status"] == "COMMITTED"


def test_concurrent_attempts_claim_once(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocked(**kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=5)
        return provider([["交易时间", "日盘 09:00-10:00"]])(**kwargs)

    results = []
    thread = threading.Thread(target=lambda: results.append(observe(tmp_path, blocked)))
    thread.start()
    try:
        assert entered.wait(timeout=5)
        assert observe(tmp_path, blocked, run_id="c" * 32)["status"] == "NOT_COMMITTED"
    finally:
        release.set()
        thread.join(timeout=5)
    assert results[0]["status"] == "COMMITTED" and calls == [1]


def test_oversize_provider_field_is_not_retained(tmp_path):
    def oversized(**kwargs):
        result = provider([["交易时间", "日盘 09:00-10:00"]])(**kwargs)
        field = result.candidate.fields[0]
        huge = replace(field, raw_value="x" * 4097, value="x" * 4097)
        return replace(result, candidate=replace(result.candidate, fields=(huge,)))

    assert observe(tmp_path, oversized)["status"] == "ATTEMPT_UNKNOWN"
    assert show(tmp_path)["status"] == "ATTEMPT_UNKNOWN"


def test_oversize_and_symlink_directory_fail_closed(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "akshare-observations").symlink_to(target, target_is_directory=True)
    calls = []
    assert observe(tmp_path, provider(calls=calls))["status"] == "NOT_COMMITTED"
    assert not calls


def test_child_process_fake_capture_offline(tmp_path):
    code = """
import sys
from datetime import date
from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract, _decode
from bt_api_ctp._calendar_child_supervisor import supervise
from bt_api_py import trading_calendar_akshare_observation as o

def collector(**kwargs):
    return collect_akshare_contract(**kwargs, _runner=lambda symbol, timeout: supervise(
        [sys.executable, "-c", "print('{\\\"status\\\":\\\"OK\\\",\\\"rows\\\":[[\\\"交易时间\\\",\\\"日盘 09:00-10:00\\\"]]}')"],
        timeout, 10000, _decode))
r=o.observe(root=sys.argv[1], observation_id="a"*32, run_id="b"*32, venue="SHFE", contract="RB2701", requested_date=date(2026,11,13), timeout=5, filesystem=o.TRUSTED_LOCAL_POSIX, _collector=collector)
print(r["status"])
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "COMMITTED"
    assert show(tmp_path)["observation_status"] == "SUCCESS"


def test_rehashed_status_tamper_is_replayed(tmp_path):
    assert observe(tmp_path, provider([["交易时间", "日盘 09:00-10:00"]]))["status"] == "COMMITTED"
    path = tmp_path / "akshare-observations" / f"result-{OID}.json"
    body, _ = obs.storage._unwrap(path.read_bytes())
    body["status"] = "MISSING_TRADING_TIME"
    body["time_role"] = "DIAGNOSTIC"
    path.write_bytes(obs.storage._envelope(body))
    assert show(tmp_path)["status"] == "UNKNOWN"


def test_rehashed_typed_rows_must_match_response_digest(tmp_path):
    assert observe(tmp_path, provider([["交易时间", "日盘 09:00-10:00"]]))["status"] == "COMMITTED"
    path = tmp_path / "akshare-observations" / f"result-{OID}.json"
    body, _ = obs.storage._unwrap(path.read_bytes())
    body["content"]["raw_rows"][0][1] = "日盘 09:00-11:00"
    import hashlib

    body["content_sha256"] = hashlib.sha256(obs.storage._json(body["content"])).hexdigest()
    path.write_bytes(obs.storage._envelope(body))
    assert show(tmp_path)["status"] == "UNKNOWN"


def test_numeric_time_remains_missing_after_typed_replay(tmp_path):
    assert (
        observe(tmp_path, provider([["交易时间", 123]]))["observation_status"]
        == "MISSING_TRADING_TIME"
    )
    result = show(tmp_path, include_content=True)
    assert result["observation_status"] == "MISSING_TRADING_TIME"
    assert result["content"]["raw_rows"] == [["交易时间", 123]]
    assert result["content"]["fields"][0]["raw_value"] == "123"
    assert result["classification_check"] == "REPLAYED"


def test_missing_typed_rows_never_becomes_success(tmp_path):
    def legacy(**kwargs):
        return replace(provider([["交易时间", "日盘 09:00-10:00"]])(**kwargs), raw_rows=None)

    assert observe(tmp_path, legacy)["status"] == "ATTEMPT_UNKNOWN"
    assert show(tmp_path)["status"] == "ATTEMPT_UNKNOWN"


@pytest.mark.parametrize("write_unknown", [False, True])
def test_oversize_capture_records_explicit_omission(tmp_path, monkeypatch, write_unknown):
    if write_unknown:
        real = obs.storage._write

        def uncertain(*args):
            result = real(*args)
            return (
                "COMMIT_UNKNOWN"
                if args[2].startswith("result-") and result == "COMMITTED"
                else result
            )

        monkeypatch.setattr(obs.storage, "_write", uncertain)
    rows = [["交易时间", "日盘 09:00-10:00"]] + [[f"field{i}", "x" * 3600] for i in range(500)]
    assert len(json.dumps({"status": "OK", "rows": rows}).encode()) < 2_200_000
    result = observe(tmp_path, provider(rows))
    assert result["record_state"] == ("COMMIT_UNKNOWN" if write_unknown else "COMMITTED")
    assert result["observation_status"] == "UNKNOWN"
    assert result["reported_observation_status"] == "SUCCESS"
    assert result["capture_state"] == "CAPTURE_TOO_LARGE"
    assert result["classification_check"] == "NOT_POSSIBLE"
    assert result["automatic_retry"] == "NOT_PROVEN_SAFE"
    assert result["requires_readback"]
    assert (result["observation_id"], result["run_id"]) == (OID, RID)
    assert result["data_state"] == "DATA_OMITTED"
    reread = show(tmp_path, include_content=True)
    assert reread["status"] == "VISIBLE_UNAUTHENTICATED"
    assert reread["capture_state"] == "CAPTURE_TOO_LARGE"
    assert reread["observation_status"] == "UNKNOWN"
    assert reread["reported_observation_status"] == "SUCCESS"
    assert reread["classification_check"] == "NOT_POSSIBLE"
    assert reread["automatic_retry"] == "NOT_PROVEN_SAFE" and reread["content"] is None


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize(
    "stage", ["intent_before", "intent_after", "result_before", "result_after"]
)
def test_process_control_propagates_during_writes(tmp_path, monkeypatch, exception, stage):
    real = obs.storage._write

    def interrupt(*args):
        target = "intent" if args[2].startswith("intent-") else "result"
        if stage == target + "_before":
            raise exception()
        result = real(*args)
        if stage == target + "_after":
            raise exception()
        return result

    monkeypatch.setattr(obs.storage, "_write", interrupt)
    with pytest.raises(exception) as caught:
        observe(tmp_path, provider([["交易时间", "日盘 09:00-10:00"]]))
    assert caught.value.observation_id == OID and caught.value.run_id == RID
    assert caught.value.record_state == "COMMIT_UNKNOWN"
    expected = {
        "intent_before": "NOT_OBSERVED",
        "intent_after": "ATTEMPT_UNKNOWN",
        "result_before": "ATTEMPT_UNKNOWN",
        "result_after": "VISIBLE_UNAUTHENTICATED",
    }
    assert show(tmp_path)["status"] == expected[stage]


def test_actual_sigint_propagates_with_child_cleanup(tmp_path):
    script = """
import json,sys
from datetime import date
from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract, _decode
from bt_api_ctp._calendar_child_supervisor import supervise
from bt_api_py import trading_calendar_akshare_observation as o
child="import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(30)"
def collector(**kwargs):
    return collect_akshare_contract(**kwargs, _runner=lambda symbol, timeout: supervise([sys.executable,'-c',child,sys.argv[2]], timeout, 10000, _decode))
try:
    o.observe(root=sys.argv[1],observation_id='a'*32,run_id='b'*32,venue='SHFE',contract='RB2701',requested_date=date(2026,11,13),timeout=20,filesystem=o.TRUSTED_LOCAL_POSIX,_collector=collector)
except KeyboardInterrupt as e:
    print(json.dumps([e.observation_id,e.run_id,e.record_state,getattr(e,'child_cleanup','MISSING')]))
"""
    pidfile = tmp_path / "child-pid"
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), str(pidfile)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    pid = None
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        pid = int(pidfile.read_text())
        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=5)
        assert not stderr
        assert json.loads(stdout) == [OID, RID, "ATTEMPT_UNKNOWN", "CHILD_CLEANUP_UNCERTAIN"]
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        pid = None
        assert show(tmp_path)["status"] == "ATTEMPT_UNKNOWN"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def test_cross_process_claim_race_prevents_second_capture(tmp_path):
    script = """
import sys,time
from pathlib import Path
from datetime import date
from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract
from bt_api_py import trading_calendar_akshare_observation as o
root=Path(sys.argv[1]); role=sys.argv[2]
def collector(**kwargs):
    (root / ('called-'+role)).write_text('called')
    if role == 'first':
        deadline=time.monotonic()+8
        while not (root/'release').exists() and time.monotonic()<deadline: time.sleep(0.01)
    return collect_akshare_contract(**kwargs,_runner=lambda *_: ('CHILD_OK',{'status':'OK','rows':[]}))
r=o.observe(root=str(root),observation_id='a'*32,run_id=('b' if role=='first' else 'c')*32,venue='SHFE',contract='RB2701',requested_date=date(2026,11,13),timeout=5,filesystem=o.TRUSTED_LOCAL_POSIX,_collector=collector)
print(r['record_state'])
"""
    first = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), "first"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "called-first").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "called-first").exists()
        second = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), "second"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        assert second.stdout.strip() == "NOT_COMMITTED"
        assert not (tmp_path / "called-second").exists()
        (tmp_path / "release").touch()
        stdout, stderr = first.communicate(timeout=5)
        assert stdout.strip() == "COMMITTED" and not stderr
        assert show(tmp_path)["observation_status"] == "EMPTY"
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()
