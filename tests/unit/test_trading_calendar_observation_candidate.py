"""Offline /2 run retention state, binding and crash semantics."""

import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import replace

import pytest

from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_preview import PreviewReport
from tests.unit.test_trading_calendar_observation_run import (
    ID,
    cli,
    env_without_network,
    run_args,
    show,
)
from tests.unit.test_trading_calendar_preview_catalog import setup_preview
from tests.unit.test_trading_calendar_preview_reports import report

CID = "b" * 32


def run_v2(root, config, supplement):
    return runs.run_once(**run_args(root, config, supplement), candidate_id=CID)


def v2_report(status, reason="NONE"):
    return replace(report(), candidate_id=CID, candidate_status=status, candidate_reason=reason)


def test_real_cli_emits_both_ids_before_any_directory_creation(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    waiting, release = tmp_path / "waiting", tmp_path / "release"
    env = env_without_network(
        tmp_path,
        f"""
import os, time
_mkdir = os.mkdir
def delayed(path, *args, **kwargs):
    if path == "observation-runs":
        open({str(waiting)!r}, "w").close()
        while not os.path.exists({str(release)!r}): time.sleep(.01)
    return _mkdir(path, *args, **kwargs)
os.mkdir = delayed
""",
    )
    process = subprocess.Popen(
        cli(root, config, supplement, "--retain-candidate"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        allocated = json.loads(process.stdout.readline())
        deadline = time.monotonic() + 10
        while not waiting.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert waiting.exists() and not (root / "observation-runs").exists()
        assert runs._id(allocated["run_id"]) and runs._id(allocated["candidate_id"])
        release.touch()
        out, err = process.communicate(timeout=25)
        assert process.returncode == 10, (out, err)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_real_cli_allocates_both_ids_before_creation_and_retains_candidate(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = env_without_network(tmp_path)
    process = subprocess.Popen(
        cli(root, config, supplement, "--retain-candidate"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    first = json.loads(process.stdout.readline())
    remaining, err = process.communicate(timeout=25)
    result = json.loads(remaining)
    assert process.returncode == 10, (first, result, err)
    assert first["event"] == "RUN_ID_ALLOCATED"
    assert first["run_id"] != first["candidate_id"]
    assert (result["run_id"], result["candidate_id"]) == (first["run_id"], first["candidate_id"])
    assert result["candidate_state"] == "COMMITTED"
    observed = show(root, first["run_id"])
    assert observed["candidate_id"] == first["candidate_id"]
    assert observed["candidate_visibility"] == "VISIBLE_UNAUTHENTICATED"
    assert (
        observed["candidate_association"] == observed["directory_physical_identity"] == "NOT_PROVEN"
    )
    assert observed["observation"]["schema"] == runs._RESULT_SCHEMA_V2
    assert observed["observation"]["candidate_state"] == "COMMITTED"
    assert observed["candidate_authorization"] == "NOT_PROVEN"
    assert all(
        str(p) not in json.dumps(first) + remaining + err for p in (root, config, supplement)
    )
    assert not (root / "CURRENT.json").exists()


def test_real_existing_candidate_id_is_not_overwritten_or_attributed(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = env_without_network(
        tmp_path,
        """
import os, uuid
if os.environ.get("ITER67_TEST_CANDIDATE_ID"):
    _real_uuid = uuid.uuid4
    _values = iter([uuid.UUID(hex="c" * 32), uuid.UUID(hex=os.environ["ITER67_TEST_CANDIDATE_ID"])])
    def selected_uuid():
        try: return next(_values)
        except StopIteration: return _real_uuid()
    uuid.uuid4 = selected_uuid
""",
    )
    first = subprocess.run(
        cli(root, config, supplement, "--retain-candidate"),
        capture_output=True,
        text=True,
        timeout=25,
        env=env,
    )
    assert first.returncode == 10, first.stderr
    allocated = json.loads(first.stdout.splitlines()[0])
    candidate = root / "inbox" / f"candidate-{allocated['candidate_id']}.json"
    before = candidate.read_bytes()
    env["ITER67_TEST_CANDIDATE_ID"] = allocated["candidate_id"]
    second = subprocess.run(
        cli(root, config, supplement, "--retain-candidate"),
        capture_output=True,
        text=True,
        timeout=25,
        env=env,
    )
    assert second.returncode == 24, (second.stdout, second.stderr)
    new_id, result = map(json.loads, second.stdout.splitlines())
    assert new_id["candidate_id"] == allocated["candidate_id"]
    assert result["candidate_state"] == "NOT_COMMITTED" and candidate.read_bytes() == before
    observed = show(root, new_id["run_id"])
    assert observed["candidate_visibility"] == "VISIBLE_UNAUTHENTICATED"
    assert observed["candidate_association"] == "NOT_PROVEN"
    assert observed["observation"]["preview"]["candidate_reason"] == "ID_EXISTS"


@pytest.mark.parametrize("after_write", [False, True])
def test_candidate_writer_exception_is_unknown_before_or_after_write(tmp_path, after_write):
    root, _, config, supplement = setup_preview(tmp_path)
    env = env_without_network(
        tmp_path,
        f"""
from bt_api_py import trading_calendar_candidate_inbox as inbox
_original = inbox.retain_candidate
def failing(*args, **kwargs):
    if {after_write!r}: _original(*args, **kwargs)
    raise OSError("injected")
inbox.retain_candidate = failing
""",
    )
    result = subprocess.run(
        cli(root, config, supplement, "--retain-candidate"),
        capture_output=True,
        text=True,
        timeout=25,
        env=env,
    )
    assert result.returncode == 25, (result.stdout, result.stderr)
    allocated, final = map(json.loads, result.stdout.splitlines())
    assert final["candidate_state"] == "COMMIT_UNKNOWN"
    observed = show(root, allocated["run_id"])
    assert observed["candidate_visibility"] == (
        "VISIBLE_UNAUTHENTICATED" if after_write else "UNKNOWN"
    )
    assert observed["candidate_association"] == "NOT_PROVEN"


@pytest.mark.parametrize(
    "worker,expected",
    [
        (v2_report("COMMITTED"), "COMMITTED"),
        (v2_report("NOT_COMMITTED", "ID_EXISTS"), "NOT_COMMITTED"),
        (v2_report("COMMIT_UNKNOWN", "IO_ERROR"), "COMMIT_UNKNOWN"),
        (v2_report("IN_PROGRESS"), "COMMIT_UNKNOWN"),
        (v2_report("NOT_ATTEMPTED"), "COMMIT_UNKNOWN"),
        (PreviewReport("ERROR", "WORKER_PROTOCOL_ERROR", 22), "COMMIT_UNKNOWN"),
        (PreviewReport("ERROR", "WORKER_COMMUNICATION_ERROR", 22), "COMMIT_UNKNOWN"),
        (
            replace(v2_report("COMMITTED"), reason="BUDGET_EXHAUSTED", exit_code=21),
            "COMMIT_UNKNOWN",
        ),
        (PreviewReport("ERROR", "SUPERVISOR_UNSUPPORTED", 22), "NOT_ATTEMPTED"),
        (PreviewReport("ERROR", "WORKER_START_FAILED", 22), "NOT_ATTEMPTED"),
        (
            PreviewReport(
                "ERROR", "WORKER_START_FAILED", 22, candidate_id=CID, candidate_status="COMMITTED"
            ),
            "COMMIT_UNKNOWN",
        ),
        (
            PreviewReport(
                "ERROR",
                "SUPERVISOR_UNSUPPORTED",
                22,
                candidate_id=CID,
                candidate_status="COMMIT_UNKNOWN",
                candidate_reason="IO_ERROR",
            ),
            "COMMIT_UNKNOWN",
        ),
    ],
)
def test_candidate_worker_status_is_conservative(tmp_path, monkeypatch, worker, expected):
    root, _, config, supplement = setup_preview(tmp_path)
    captured = []
    monkeypatch.setattr(runs, "_supervise", lambda args: (captured.append(args), worker)[1])
    output = run_v2(root, config, supplement)
    assert output["candidate_state"] == expected
    assert captured[0]["candidate_id"] == CID and captured[0]["trusted_local_posix"] is True
    observed = show(root)
    assert observed["observation"]["candidate_state"] == expected
    assert observed["candidate_association"] == "NOT_PROVEN"
    assert observed["candidate_visibility"] == "UNKNOWN"


def test_exception_and_interrupt_do_not_claim_no_commit(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)

    def fail(_):
        raise RuntimeError("private")

    monkeypatch.setattr(runs, "_supervise", fail)
    result = run_v2(root, config, supplement)
    assert result["candidate_state"] == "COMMIT_UNKNOWN"
    assert show(root)["observation"]["candidate_state"] == "COMMIT_UNKNOWN"
    (tmp_path / "other").mkdir()
    root2, _, config2, supplement2 = setup_preview(tmp_path / "other")

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(runs, "_supervise", interrupt)
    result2 = run_v2(root2, config2, supplement2)
    assert result2["candidate_state"] == "COMMIT_UNKNOWN"
    assert result2["record_installation"] == "NOT_ATTEMPTED"
    assert show(root2)["candidate_id"] == CID


def test_real_cli_sigint_retains_candidate_id_and_unknown(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    marker = tmp_path / "worker-pid"
    env = env_without_network(
        tmp_path,
        f"""
import subprocess, sys
import bt_api_py.trading_calendar_preview as preview
_original = subprocess.Popen
def spawn(command, **kwargs):
    if command[1:] == ["-m", "bt_api_py.trading_calendar_preview", "--_worker"]:
        child = _original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        with open({str(marker)!r}, "w") as stream: stream.write(str(child.pid))
        return child
    return _original(command, **kwargs)
preview.subprocess.Popen = spawn
""",
    )
    process = subprocess.Popen(
        cli(root, config, supplement, "--retain-candidate"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    child_pid = None
    try:
        first = json.loads(process.stdout.readline())
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        child_pid = int(marker.read_text())
        os.kill(process.pid, signal.SIGINT)
        out, err = process.communicate(timeout=10)
        assert process.returncode == 23, (out, err)
        event = json.loads(out)
        assert event["candidate_id"] == first["candidate_id"]
        assert event["candidate_state"] == "COMMIT_UNKNOWN"
        assert event["record_installation"] == "NOT_ATTEMPTED"
        observed = show(root, first["run_id"])
        assert observed["candidate_id"] == first["candidate_id"]
        assert observed["record"] == "NOT_OBSERVED"
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        child_pid = None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if child_pid is not None:
            try:
                if os.getpgid(child_pid) == child_pid:
                    os.killpg(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_mixed_versions_and_forged_state_fail_closed(tmp_path):
    intent = runs._valid_intent(runs._intent(ID, False, CID), ID)
    v1 = runs._valid_intent(runs._intent(ID, False), ID)
    raw_v1 = runs._result(ID, v1, report())
    raw_v2 = runs._result(ID, intent, v2_report("COMMITTED"))
    with pytest.raises(ValueError):
        runs._valid_intent(runs._intent(ID, False, ID), ID)
    with pytest.raises(ValueError):
        runs._valid_result(raw_v1, ID, intent)
    with pytest.raises(ValueError):
        runs._valid_result(raw_v2, ID, v1)
    for key, value in [
        ("candidate_id", "c" * 32),
        ("intent_sha256", "0" * 64),
        ("candidate_state", "NOT_COMMITTED"),
        ("candidate_association", "PROVEN"),
    ]:
        envelope = json.loads(raw_v2)
        envelope["body"][key] = value
        envelope["sha256"] = hashlib.sha256(runs._canonical(envelope["body"])).hexdigest()
        with pytest.raises(ValueError):
            runs._valid_result(runs._canonical(envelope), ID, intent)


def test_existing_candidate_and_duplicate_run_not_claimed_created(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda args: v2_report("NOT_COMMITTED", "ID_EXISTS"))
    result = run_v2(root, config, supplement)
    assert result["candidate_state"] == "NOT_COMMITTED"
    assert show(root)["candidate_association"] == "NOT_PROVEN"
    result2 = run_v2(root, config, supplement)
    assert result2["reason"] == "ID_EXISTS" and result2["candidate_state"] == "NOT_ATTEMPTED"


@pytest.mark.parametrize("call_number", [1, 2])
def test_link_failure_after_commit_is_unknown_and_id_reconciles(tmp_path, monkeypatch, call_number):
    root, _, config, supplement = setup_preview(tmp_path)
    original = runs.os.link
    calls = []

    def fail_after(*args, **kwargs):
        original(*args, **kwargs)
        calls.append(True)
        if len(calls) == call_number:
            raise OSError("injected")

    monkeypatch.setattr(runs.os, "link", fail_after)
    monkeypatch.setattr(runs, "_supported", lambda: True)
    monkeypatch.setattr(runs, "_supervise", lambda _: v2_report("COMMITTED"))
    result = run_v2(root, config, supplement)
    observed = show(root)
    assert observed["candidate_id"] == CID
    if call_number == 1:
        assert result["intent_installation"] == "COMMIT_UNKNOWN"
        assert result["candidate_state"] == "NOT_ATTEMPTED"
        assert observed["record"] == "NOT_OBSERVED"
    else:
        assert result["record_installation"] == "COMMIT_UNKNOWN"
        assert result["candidate_state"] == "COMMITTED"
        assert observed["record"] == "VISIBLE_UNAUTHENTICATED"
    assert result["retry_policy"] == "RECONCILE_FIRST"


def test_v2_list_reads_binding_without_authenticating_candidate(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda _: v2_report("NOT_COMMITTED", "ID_EXISTS"))
    run_v2(root, config, supplement)
    listing = runs.list_runs(root=str(root), filesystem=runs.TRUSTED_LOCAL_POSIX)
    assert listing["status"] == "OBSERVED_UNAUTHENTICATED"
    assert listing["runs"][0]["candidate_id"] == CID
    assert listing["source_authentication"] == "NOT_PROVEN"


def test_v2_directory_replaced_after_preview_is_unknown(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)

    def swap(_):
        root.rename(tmp_path / "old-root")
        root.mkdir(mode=0o700)
        return v2_report("COMMITTED")

    monkeypatch.setattr(runs, "_supervise", swap)
    result = run_v2(root, config, supplement)
    assert result["reason"] == "DIRECTORY_CHANGED" and result["candidate_state"] == "COMMIT_UNKNOWN"
    assert result["record_installation"] == "NOT_ATTEMPTED"
    assert not (root / "CURRENT.json").exists()
