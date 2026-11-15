"""Explicit /3 run observation and /6 synthetic navigation, never real network."""

import hashlib
import json
import os
import subprocess
import time
from dataclasses import replace

import pytest

from bt_api_py import trading_calendar_candidate_inbox as inbox
from bt_api_py import trading_calendar_observation_run as runs
from tests.unit.test_trading_calendar_observation_run import ID, cli, run_args, show
from tests.unit.test_trading_calendar_preview_catalog import setup_preview
from tests.unit.test_trading_calendar_preview_network_navigation import inject
from tests.unit.test_trading_calendar_preview_reports import report

CID = "b" * 32


def flags():
    return ["--retain-candidate", "--fetch-product-navigation"]


def read_listing(root):
    return runs.list_runs(root=str(root), filesystem=runs.TRUSTED_LOCAL_POSIX)


def test_ids_are_flushed_before_write_and_v3_reaches_v6(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    waiting, release = tmp_path / "waiting", tmp_path / "release"
    with (tmp_path / "inject-network-nav" / "sitecustomize.py").open("a") as file:
        file.write(f"""
import os, time
_original_mkdir = os.mkdir
def delayed(path, *args, **kwargs):
    if path == "observation-runs":
        open({str(waiting)!r}, "w").close()
        while not os.path.exists({str(release)!r}): time.sleep(.01)
    return _original_mkdir(path, *args, **kwargs)
os.mkdir = delayed
""")
    process = subprocess.Popen(
        cli(root, config, supplement, *flags()),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        first = json.loads(process.stdout.readline())
        deadline = time.monotonic() + 10
        while not waiting.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert waiting.exists() and not (root / "observation-runs").exists()
        assert first["navigation_requested"] is True
        rid, cid = first["run_id"], first["candidate_id"]
        assert runs._id(rid) and runs._id(cid) and rid != cid
        release.touch()
        output, err = process.communicate(timeout=25)
        assert process.returncode == 10, (output, err)
        final = json.loads(output)
        assert final["navigation_requested"] is True and final["candidate_state"] == "COMMITTED"
        observed = show(root, rid)
        assert observed["navigation_requested"] is True
        assert observed["candidate_visibility"] == "VISIBLE_UNAUTHENTICATED"
        assert observed["candidate_association"] == "NOT_PROVEN"
        assert observed["observation"]["schema"] == runs._RESULT_SCHEMA_V3
        assert observed["observation"]["navigation_requested"] is True
        assert observed["observation"]["preview"]["source_status"] == "NOT_REQUESTED"
        intent = json.loads((root / "observation-runs" / f"run-{rid}.intent").read_bytes())
        assert intent["body"]["schema"] == runs._INTENT_SCHEMA_V3
        assert intent["body"]["candidate_id"] == cid
        candidate = inbox.read_candidate(
            root=root, candidate_id=cid, filesystem="TRUSTED_LOCAL_POSIX"
        )
        assert candidate["schema"].endswith("/6")
        assert candidate["payload"]["navigation_fetch"]["status"] == "CANDIDATE"
        listed = read_listing(root)
        assert listed["runs"][0]["navigation_requested"] is True
        assert (tmp_path / "nav-called").read_text() == "1"
        assert not (root / "CURRENT.json").exists()
        assert all(
            str(p) not in json.dumps(first) + output + err for p in (root, config, supplement)
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_flag_requires_candidate_and_rejects_before_ids_or_network(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    process = subprocess.run(
        cli(root, config, supplement, "--fetch-product-navigation"),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    assert process.returncode == 2
    assert json.loads(process.stdout) == {"status": "ERROR", "reason": "INVALID_ARGUMENTS"}
    assert not (root / "observation-runs").exists() and not (tmp_path / "nav-called").exists()
    value = runs.run_once(**run_args(root, config, supplement), fetch_product_navigation=True)
    assert (
        value["reason"] == "INVALID_ARGUMENTS" and value["intent_installation"] == "NOT_ATTEMPTED"
    )
    missing_trust = [
        arg for arg in cli(root, config, supplement, *flags()) if arg != "--trusted-local-posix"
    ]
    process = subprocess.run(missing_trust, capture_output=True, text=True, timeout=15, env=env)
    assert process.returncode == 2 and not (root / "observation-runs").exists()
    assert not (tmp_path / "nav-called").exists()


@pytest.mark.parametrize("enabled", [False, True])
def test_navigation_flag_passed_separately_from_shfe(tmp_path, monkeypatch, enabled):
    root, _, config, supplement = setup_preview(tmp_path)
    captured = []
    sample = replace(report(), candidate_id=CID, candidate_status="COMMITTED")
    monkeypatch.setattr(runs, "_supervise", lambda args: (captured.append(args), sample)[1])
    result = runs.run_once(
        **run_args(root, config, supplement, enabled=enabled),
        candidate_id=CID,
        fetch_product_navigation=True,
    )
    assert result["record_installation"] == "COMMITTED"
    assert captured[0]["enable_shfe"] is enabled and captured[0]["fetch_product_navigation"] is True
    assert captured[0]["budget_seconds"] == 15
    assert captured[0]["candidate_id"] == CID and captured[0]["trusted_local_posix"] is True
    assert show(root)["observation"]["navigation_requested"] is True


def test_old_versions_byte_and_field_contract_remain_unchanged():
    first = runs._intent(ID, False)
    second = runs._intent(ID, False, CID)
    v1 = runs._valid_intent(first, ID)
    v2 = runs._valid_intent(second, ID)
    assert v1["schema"] == runs._INTENT_SCHEMA and "navigation_requested" not in v1
    assert v2["schema"] == runs._INTENT_SCHEMA_V2 and "navigation_requested" not in v2
    assert first == runs._canonical(
        {"body": v1, "sha256": hashlib.sha256(runs._canonical(v1)).hexdigest()}
    )
    assert second == runs._canonical(
        {"body": v2, "sha256": hashlib.sha256(runs._canonical(v2)).hexdigest()}
    )
    result1 = runs._valid_result(runs._result(ID, v1, report()), ID, v1)
    result2 = runs._valid_result(
        runs._result(ID, v2, replace(report(), candidate_id=CID, candidate_status="COMMITTED")),
        ID,
        v2,
    )
    assert result1["schema"] == runs._RESULT_SCHEMA and "navigation_requested" not in result1
    assert result2["schema"] == runs._RESULT_SCHEMA_V2 and "navigation_requested" not in result2


def test_version_id_and_hash_tampering_cannot_upgrade_v3():
    intents = [
        runs._valid_intent(runs._intent(ID, False, *args), ID) for args in [(), (CID,), (CID, True)]
    ]
    reports = [
        report(),
        replace(report(), candidate_id=CID, candidate_status="COMMITTED"),
        replace(report(), candidate_id=CID, candidate_status="COMMITTED"),
    ]
    encoded = [runs._result(ID, intent, value) for intent, value in zip(intents, reports)]  # noqa: B905 - Python 3.9
    for index, raw in enumerate(encoded):
        for other, intent in enumerate(intents):
            if index == other:
                assert runs._valid_result(raw, ID, intent)
            else:
                with pytest.raises(ValueError):
                    runs._valid_result(raw, ID, intent)
    for key, value in [("candidate_id", ID), ("run_id", "c" * 32), ("navigation_requested", False)]:
        envelope = json.loads(runs._intent(ID, False, CID, True))
        envelope["body"][key] = value
        envelope["sha256"] = hashlib.sha256(runs._canonical(envelope["body"])).hexdigest()
        with pytest.raises(ValueError):
            runs._valid_intent(runs._canonical(envelope), ID)
    for key, value in [
        ("candidate_id", "c" * 32),
        ("run_id", "c" * 32),
        ("intent_sha256", "0" * 64),
        ("navigation_requested", False),
    ]:
        envelope = json.loads(encoded[2])
        envelope["body"][key] = value
        envelope["sha256"] = hashlib.sha256(runs._canonical(envelope["body"])).hexdigest()
        with pytest.raises(ValueError):
            runs._valid_result(runs._canonical(envelope), ID, intents[2])


@pytest.mark.parametrize("link_failure", [1, 2])
def test_partial_install_preserves_v3_request_without_false_completion(
    tmp_path, monkeypatch, link_failure
):
    root, _, config, supplement = setup_preview(tmp_path)
    real_link = os.link
    calls = []

    def failing(*args, **kwargs):
        real_link(*args, **kwargs)
        calls.append(True)
        if len(calls) == link_failure:
            raise OSError("injected after link")

    monkeypatch.setattr(runs.os, "link", failing)
    monkeypatch.setattr(runs, "_supported", lambda: True)
    sample = replace(
        report(), candidate_id=CID, candidate_status="COMMIT_UNKNOWN", candidate_reason="IO_ERROR"
    )
    monkeypatch.setattr(runs, "_supervise", lambda args: sample)
    value = runs.run_once(
        **run_args(root, config, supplement), candidate_id=CID, fetch_product_navigation=True
    )
    seen = show(root)
    assert seen["navigation_requested"] is True and seen["candidate_id"] == CID
    if link_failure == 1:
        assert value["intent_installation"] == "COMMIT_UNKNOWN" and seen["record"] == "NOT_OBSERVED"
    else:
        assert (
            value["record_installation"] == "COMMIT_UNKNOWN"
            and seen["record"] == "VISIBLE_UNAUTHENTICATED"
        )
        assert seen["observation"]["candidate_state"] == "COMMIT_UNKNOWN"
    assert read_listing(root)["runs"][0]["navigation_requested"] is True


def test_existing_candidate_visibility_does_not_upgrade_unknown_commit(tmp_path, monkeypatch):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    created = subprocess.run(
        cli(root, config, supplement, *flags()), capture_output=True, text=True, timeout=25, env=env
    )
    assert created.returncode == 10, (created.stdout, created.stderr)
    first = json.loads(created.stdout.splitlines()[0])
    cid = first["candidate_id"]
    uncertain = replace(
        report(), candidate_id=cid, candidate_status="COMMIT_UNKNOWN", candidate_reason="IO_ERROR"
    )
    monkeypatch.setattr(runs, "_supervise", lambda args: uncertain)
    value = runs.run_once(
        **run_args(root, config, supplement), candidate_id=cid, fetch_product_navigation=True
    )
    assert value["candidate_state"] == "COMMIT_UNKNOWN"
    observed = show(root)
    assert observed["candidate_visibility"] == "VISIBLE_UNAUTHENTICATED"
    assert observed["candidate_association"] == "NOT_PROVEN"
    assert observed["observation"]["candidate_state"] == "COMMIT_UNKNOWN"


@pytest.mark.parametrize("retain", [False, True])
def test_default_run_does_not_request_navigation(tmp_path, retain):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    extra = ["--retain-candidate"] if retain else []
    result = subprocess.run(
        cli(root, config, supplement, *extra), capture_output=True, text=True, timeout=25, env=env
    )
    assert result.returncode == 10, (result.stdout, result.stderr)
    assert not (tmp_path / "nav-called").exists()
    first = json.loads(result.stdout.splitlines()[0])
    observed = show(root, first["run_id"])
    assert "navigation_requested" not in observed
    assert observed["observation"]["schema"] == (
        runs._RESULT_SCHEMA_V2 if retain else runs._RESULT_SCHEMA
    )


def test_real_shfe_request_and_navigation_are_independent(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    with (tmp_path / "inject-network-nav" / "sitecustomize.py").open("a") as file:
        file.write("""
if "--_worker" in sys.argv:
    from types import SimpleNamespace
    import bt_api_ctp.calendar_shfe_fetch as hours
    def unavailable(**kwargs):
        return SimpleNamespace(status="SUPERVISOR_UNSUPPORTED", transport_status="SUPERVISOR_UNSUPPORTED", verification="UNVERIFIED", coverage="NOT_PROVEN", page=None)
    hours.collect_shfe_page = unavailable
    hours.collect_shfe_holiday = unavailable
""")
    result = subprocess.run(
        cli(root, config, supplement, "--enable-shfe", *flags()),
        capture_output=True,
        text=True,
        timeout=25,
        env=env,
    )
    assert result.returncode == 10, (result.stdout, result.stderr)
    first = json.loads(result.stdout.splitlines()[0])
    observed = show(root, first["run_id"])
    body = observed["observation"]
    assert body["shfe_requested"] is True and body["navigation_requested"] is True
    assert (
        body["preview"]["source_status"]
        == body["preview"]["holiday_status"]
        == "SUPERVISOR_UNSUPPORTED"
    )
    candidate = inbox.read_candidate(
        root=root, candidate_id=first["candidate_id"], filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert candidate["payload"]["navigation_fetch"]["status"] == "CANDIDATE"
    assert not (root / "CURRENT.json").exists()


@pytest.mark.parametrize("mode", ["cleanup", "hang"])
def test_navigation_uncertain_cleanup_never_upgrades_candidate_visibility(tmp_path, mode):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["NAV_MODE"] = mode
    cmd = cli(root, config, supplement, *flags())
    cmd[cmd.index("15")] = "3"
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=env)
    assert result.returncode == 25, (result.stdout, result.stderr)
    first, final = map(json.loads, result.stdout.splitlines())
    assert final["candidate_state"] == "COMMIT_UNKNOWN" and final["navigation_requested"] is True
    seen = show(root, first["run_id"])
    assert (
        seen["navigation_requested"] is True
        and seen["observation"]["candidate_state"] == "COMMIT_UNKNOWN"
    )
    assert seen["candidate_association"] == "NOT_PROVEN"
    assert seen["candidate_visibility"] == (
        "VISIBLE_UNAUTHENTICATED" if mode == "cleanup" else "UNKNOWN"
    )
    pid, child_group, worker_group = map(int, (tmp_path / "child").read_text().split())
    assert child_group == worker_group
    for _ in range(30):
        proc = subprocess.run(
            ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip().startswith("Z"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("test-owned descendant remained live")
    assert not (root / "CURRENT.json").exists()
