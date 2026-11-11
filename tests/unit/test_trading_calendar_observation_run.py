"""One-shot observations against synthetic files and injected ports only."""

import json
import multiprocessing
import os
import stat
import subprocess
import sys
import time

import pytest

from bt_api_py import trading_calendar_observation_run as runs
from tests.unit.test_trading_calendar_preview import run as fake_preview
from tests.unit.test_trading_calendar_preview import source
from tests.unit.test_trading_calendar_preview_catalog import setup_preview
from tests.unit.test_trading_calendar_preview_reports import report as disabled_report

ID = "a" * 32


def run_args(root, config, supplement, run_id=ID, enabled=False):
    return {
        "root": str(root),
        "config": str(config),
        "supplement": str(supplement),
        "budget_seconds": 15,
        "enable_shfe": enabled,
        "filesystem": "TRUSTED_LOCAL_POSIX",
        "run_id": run_id,
    }


def show(root, run_id=ID):
    return runs.show_once(root=str(root), run_id=run_id, filesystem="TRUSTED_LOCAL_POSIX")


def cli(root, config, supplement, *extra):
    return [
        sys.executable,
        "-m",
        "bt_api_py.trading_calendar_observation_run",
        "run",
        "--calendar-root",
        str(root),
        "--config",
        str(config),
        "--supplement",
        str(supplement),
        "--budget-seconds",
        "15",
        "--trusted-local-posix",
        *extra,
    ]


def env_without_network(tmp_path, extra=""):
    folder = tmp_path / "inject"
    folder.mkdir()
    (folder / "sitecustomize.py").write_text(
        """
import socket, smtplib
class NoNetwork(socket.socket):
    def connect(self, *args, **kwargs): raise AssertionError("network not allowed in test")
socket.socket = NoNetwork
class NoMail:
    def __init__(self, *args, **kwargs): raise AssertionError("mail not allowed in test")
smtplib.SMTP = NoMail
smtplib.SMTP_SSL = NoMail
"""
        + extra
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(folder) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def test_real_cli_allocates_before_writes_and_stores_six_unverified_venues(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = env_without_network(tmp_path)
    result = subprocess.run(
        cli(root, config, supplement),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )
    assert result.returncode == 10, (result.stdout, result.stderr)
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(lines) == 2 and lines[0]["event"] == "RUN_ID_ALLOCATED"
    rid = lines[0]["run_id"]
    assert lines[1]["run_id"] == rid
    assert (lines[1]["intent_installation"], lines[1]["record_installation"]) == (
        "COMMITTED",
        "COMMITTED",
    )
    result_show = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_observation_run",
            "show",
            "--calendar-root",
            str(root),
            "--run-id",
            rid,
            "--trusted-local-posix",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        check=False,
    )
    assert result_show.returncode == 0, result_show.stderr
    record = json.loads(result_show.stdout)
    assert record["intent"] == record["record"] == "VISIBLE_UNAUTHENTICATED"
    body = record["observation"]
    assert [row["venue"] for row in body["venues"]] == [
        "SHFE",
        "INE",
        "CZCE",
        "DCE",
        "CFFEX",
        "GFEX",
    ]
    assert all(row["state"] == "NOT_ATTEMPTED" for row in body["venues"])
    assert (
        body["verification"] == "UNVERIFIED"
        and body["coverage"] == body["source_authentication"] == "NOT_PROVEN"
    )
    assert body["last_success"] == "NOT_UPDATED" and body["publication"] == "NOT_AUTHORIZED"
    assert body["durability"] == body["writer_identity"] == body["time_ordering"] == "NOT_PROVEN"
    assert not (root / "CURRENT.json").exists()
    assert all(
        str(p) not in result.stdout + result.stderr + result_show.stdout + result_show.stderr
        for p in (root, config, supplement)
    )
    assert stat.S_IMODE((root / "observation-runs").stat().st_mode) == 0o700


def test_structural_shfe_observation_never_becomes_success_and_ine_not_inferred(
    tmp_path, monkeypatch
):
    root, _, config, supplement = setup_preview(tmp_path)
    report = fake_preview(enable_shfe=True, collector=lambda **kw: source("HTTP_OK"))
    monkeypatch.setattr(runs, "_supervise", lambda args: report)
    result = runs.run_once(**run_args(root, config, supplement, enabled=True))
    assert result["record_installation"] == "COMMITTED"
    body = show(root)["observation"]
    assert body["preview_outcome"] == "INCOMPLETE"
    assert body["venues"][0]["state"] == "PARTIAL_OBSERVATION"
    assert body["venues"][0]["hours"]["state"] == "STRUCTURAL_CANDIDATE"
    assert body["venues"][0]["holiday"]["state"] == "STRUCTURAL_CANDIDATE"
    assert all(row["state"] == "NOT_ATTEMPTED" for row in body["venues"][1:])
    assert body["last_success"] == "NOT_UPDATED" and body["publication"] == "NOT_AUTHORIZED"


def test_preview_exception_is_recorded_as_unknown_not_completion(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)

    def fail(args):
        raise RuntimeError("private /secret")

    monkeypatch.setattr(runs, "_supervise", fail)
    result = runs.run_once(**run_args(root, config, supplement, enabled=True))
    assert result["record_installation"] == "COMMITTED"
    body = show(root)["observation"]
    assert body["preview_outcome"] == "UNKNOWN" and body["preview"] is None
    assert body["venues"][0]["state"] == "UNKNOWN"
    assert "secret" not in json.dumps(result) + json.dumps(body)


def test_duplicate_id_is_not_overwritten_and_never_runs_preview_twice(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    calls = []
    monkeypatch.setattr(runs, "_supervise", lambda args: (calls.append(True), disabled_report())[1])
    first = runs.run_once(**run_args(root, config, supplement))
    result_file = root / "observation-runs" / f"run-{ID}.json"
    before = result_file.read_bytes()
    second = runs.run_once(**run_args(root, config, supplement))
    assert first["record_installation"] == "COMMITTED"
    assert (
        second["reason"] == "ID_EXISTS" and len(calls) == 1 and result_file.read_bytes() == before
    )


def test_intent_only_is_not_claimed_failed_or_completed(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    descriptors, chain = runs._open_runs(root, create=True)
    try:
        assert (
            runs._write_once(descriptors[-1], chain, f"run-{ID}.intent", runs._intent(ID, True))[0]
            == "COMMITTED"
        )
    finally:
        runs._close(descriptors)
    result = show(root)
    assert result["intent"] == "VISIBLE_UNAUTHENTICATED" and result["record"] == "NOT_OBSERVED"
    assert result["observation"] is None and result["durability"] == "NOT_PROVEN"


def test_intent_link_ambiguity_is_not_retried_or_followed_by_preview(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    original = runs.os.link
    calls = []

    def fail_after(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("private injected")

    monkeypatch.setattr(runs.os, "link", fail_after)
    monkeypatch.setattr(runs, "_supported", lambda: True)
    monkeypatch.setattr(runs, "_supervise", lambda args: calls.append(True))
    result = runs.run_once(**run_args(root, config, supplement))
    assert (
        result["intent_installation"] == "COMMIT_UNKNOWN"
        and result["record_installation"] == "NOT_ATTEMPTED"
    )
    assert result["retry_policy"] == "RECONCILE_FIRST" and not calls
    assert (
        show(root)["intent"] == "VISIBLE_UNAUTHENTICATED" and show(root)["record"] == "NOT_OBSERVED"
    )


def test_record_link_ambiguity_is_checked_by_show_without_retry(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    original = runs.os.link
    count = 0

    def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        original(*args, **kwargs)
        if count == 2:
            raise OSError("private injected")

    monkeypatch.setattr(runs.os, "link", fail_second)
    monkeypatch.setattr(runs, "_supported", lambda: True)
    monkeypatch.setattr(runs, "_supervise", lambda args: disabled_report())
    result = runs.run_once(**run_args(root, config, supplement))
    assert (
        result["intent_installation"] == "COMMITTED"
        and result["record_installation"] == "COMMIT_UNKNOWN"
    )
    assert result["retry_policy"] == "RECONCILE_FIRST"
    observed = show(root)
    assert observed["intent"] == observed["record"] == "VISIBLE_UNAUTHENTICATED"
    assert observed["durability"] == "NOT_PROVEN"


def test_root_replacement_during_preview_cannot_install_result_in_replacement(
    tmp_path, monkeypatch
):
    root, _, config, supplement = setup_preview(tmp_path)
    old = tmp_path / "old-calendar"

    def swap(args):
        root.rename(old)
        root.mkdir()
        return disabled_report()

    monkeypatch.setattr(runs, "_supervise", swap)
    result = runs.run_once(**run_args(root, config, supplement))
    assert (
        result["intent_installation"] == "COMMITTED"
        and result["record_installation"] != "COMMITTED"
    )
    assert not (root / "observation-runs" / f"run-{ID}.json").exists()
    assert (old / "observation-runs" / f"run-{ID}.intent").is_file()
    assert not (old / "observation-runs" / f"run-{ID}.json").exists()


def test_show_rejects_tampered_hash_symlink_and_permissive_file(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(runs, "_supervise", lambda args: disabled_report())
    assert runs.run_once(**run_args(root, config, supplement))["record_installation"] == "COMMITTED"
    path = root / "observation-runs" / f"run-{ID}.json"
    original = path.read_bytes()
    data = json.loads(original)
    data["body"]["venues"][1]["state"] = "STRUCTURAL_CANDIDATE"
    path.write_bytes(runs._canonical(data))
    assert show(root)["record"] == "UNKNOWN"
    path.write_bytes(original)
    path.chmod(0o644)
    assert show(root)["record"] == "UNKNOWN"
    path.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(original)
    path.symlink_to(outside)
    assert show(root)["record"] == "UNKNOWN" and outside.read_bytes() == original


def test_result_without_intent_or_symlinked_directory_never_claims_visibility(tmp_path):
    root, _, _, _ = setup_preview(tmp_path)
    folder = root / "observation-runs"
    folder.mkdir(mode=0o700)
    (folder / f"run-{ID}.json").write_text("{}")
    (folder / f"run-{ID}.json").chmod(0o600)
    result = show(root)
    assert result["intent"] == "NOT_OBSERVED" and result["record"] == "UNKNOWN"
    (folder / f"run-{ID}.json").unlink()
    folder.rmdir()
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    folder.symlink_to(outside, target_is_directory=True)
    assert show(root)["intent"] == "UNKNOWN" and not list(outside.iterdir())


def _concurrent(root, config, supplement, gate, queue):
    from bt_api_py import trading_calendar_observation_run as module
    from tests.unit.test_trading_calendar_preview_reports import report

    module._supervise = lambda args: report()
    gate.wait(10)
    queue.put(module.run_once(**run_args(root, config, supplement)))


def test_concurrent_same_id_has_single_winner_without_overwrite(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    gate, queue = ctx.Event(), ctx.Queue()
    children = [
        ctx.Process(target=_concurrent, args=(root, config, supplement, gate, queue))
        for _ in range(2)
    ]
    for child in children:
        child.start()
    gate.set()
    try:
        results = [queue.get(timeout=20) for _ in children]
    finally:
        for child in children:
            child.join(20)
    assert all(child.exitcode == 0 for child in children)
    assert sum(result["record_installation"] == "COMMITTED" for result in results) == 1
    assert sum(result["reason"] == "ID_EXISTS" for result in results) == 1
    assert show(root)["record"] == "VISIBLE_UNAUTHENTICATED"


def test_real_cli_interrupted_after_reservation_retains_id_and_intent(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = env_without_network(
        tmp_path,
        """
import time
from bt_api_py import trading_calendar_preview as p
def stalled(args): time.sleep(30)
p._supervise = stalled
""",
    )
    process = subprocess.Popen(
        cli(root, config, supplement, "--enable-shfe"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        first = json.loads(process.stdout.readline())
        rid = first["run_id"]
        intent = root / "observation-runs" / f"run-{rid}.intent"
        for _ in range(100):
            if intent.exists():
                break
            time.sleep(0.05)
        assert intent.exists() and first["event"] == "RUN_ID_ALLOCATED"
    finally:
        process.terminate()
        process.communicate(timeout=10)
    observed = show(root, rid)
    assert observed["intent"] == "VISIBLE_UNAUTHENTICATED" and observed["record"] == "NOT_OBSERVED"
    assert not (root / "CURRENT.json").exists()


def test_failed_directory_after_allocating_id_returns_it_without_paths(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    missing = root / "absent"
    result = subprocess.run(
        cli(missing, config, supplement), capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 24
    first, last = map(json.loads, result.stdout.splitlines())
    assert first["run_id"] == last["run_id"] and last["intent_installation"] == "NOT_ATTEMPTED"
    assert str(missing) not in result.stdout + result.stderr and not missing.exists()


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("WORKER_START_FAILED", "NOT_ATTEMPTED"),
        ("SUPERVISOR_UNSUPPORTED", "NOT_ATTEMPTED"),
        ("WORKER_PROTOCOL_ERROR", "UNKNOWN"),
        ("WORKER_COMMUNICATION_ERROR", "UNKNOWN"),
        ("WORKER_CLEANUP_UNCERTAIN", "UNKNOWN"),
    ],
)
def test_no_attempt_history_is_not_mistaken_for_no_attempt(reason, expected):
    from bt_api_py.trading_calendar_preview import PreviewReport

    report = PreviewReport("ERROR", reason, 23 if reason == "WORKER_CLEANUP_UNCERTAIN" else 22)
    detached = runs._report_data(report)
    assert detached is not None
    assert runs._source(detached, "hours", True)["state"] == expected
    assert runs._source(detached, "holiday", True)["state"] == expected


def test_missing_entry_requires_directory_chain_recheck(tmp_path, monkeypatch):
    root, _, _, _ = setup_preview(tmp_path)
    descriptors, chain = runs._open_runs(root, create=True)
    try:
        matches = iter([True, False])
        monkeypatch.setattr(runs, "_directory_matches", lambda _: next(matches))
        with pytest.raises(ValueError):
            runs._read_entry(descriptors[-1], chain, "missing")
    finally:
        runs._close(descriptors)
