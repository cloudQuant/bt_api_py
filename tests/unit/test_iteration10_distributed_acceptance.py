"""Focused tests for the fixed synthetic Iteration 10 two-host runner."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "iteration10_distributed_acceptance.py"
REPOSITORY = SCRIPT.parents[1]


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _run(*arguments: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        timeout=timeout,
        check=False,
    )


def _json_stdout(process: subprocess.CompletedProcess[str]) -> dict[str, object]:
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    assert lines, {"returncode": process.returncode, "stderr": process.stderr}
    result = json.loads(lines[-1])
    assert isinstance(result, dict)
    return result


def _wait_for_started(path: Path, process: subprocess.Popen[str], timeout: float = 10.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                record = None
            if isinstance(record, dict) and record.get("phase") == "started":
                return record
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(
                {"owner_exit": process.returncode, "stdout": stdout, "stderr": stderr}
            )
        time.sleep(0.05)
    raise AssertionError("synthetic owner did not become ready before the test deadline")


def test_prepare_creates_separate_synthetic_curve_bundles(tmp_path: Path) -> None:
    from scripts.iteration10_distributed_acceptance import prepare_bundle

    root = tmp_path / "prepared"
    result = prepare_bundle(root, advertise_host="127.0.0.1", port=17601)

    owner_dir = Path(result["owner_bundle"])
    strategy_dir = Path(result["strategy_bundle"])
    owner_bundle = json.loads((owner_dir / "bundle.json").read_text(encoding="utf-8"))
    client_bundle = json.loads((strategy_dir / "bundle.json").read_text(encoding="utf-8"))
    owner_keys = json.loads((owner_dir / "curve-keys.json").read_text(encoding="utf-8"))
    client_keys = json.loads((strategy_dir / "curve-keys.json").read_text(encoding="utf-8"))

    assert result["secret_material_printed"] is False
    assert owner_bundle["source_mode"] == client_bundle["source_mode"] == "synthetic-only"
    assert owner_bundle["bundle_id"] == client_bundle["bundle_id"]
    assert set(owner_keys) == {"server_public", "server_secret", "strategy_public"}
    assert set(client_keys) == {"server_public", "strategy_public", "strategy_secret"}
    assert owner_keys["server_public"] == client_keys["server_public"]
    assert owner_keys["strategy_public"] == client_keys["strategy_public"]
    assert "server_secret" not in json.dumps(owner_bundle)
    assert "strategy_secret" not in json.dumps(client_bundle)
    assert "rpc_bind" in owner_bundle["service_config"]["communication"]["profiles"][
        "i10-dist-synthetic-curve-v1"
    ]["endpoints"]
    assert client_bundle["client_config"]["communication"]["profiles"][
        "i10-dist-synthetic-curve-v1"
    ]["endpoints"]["rpc_connect"] == "tcp://127.0.0.1:17601"


@pytest.mark.integration
def test_local_curve_tcp_owner_reconnect_halt_and_second_owner_exclusion(
    tmp_path: Path,
) -> None:
    root = tmp_path / "prepared"
    prepared = _run(
        "prepare",
        "--output-dir",
        str(root),
        "--advertise-host",
        "127.0.0.1",
        "--port",
        str(_available_port()),
    )
    assert prepared.returncode == 0, {"stdout": prepared.stdout, "stderr": prepared.stderr}
    prepared_record = _json_stdout(prepared)
    owner_dir = Path(str(prepared_record["owner_bundle"]))
    strategy_dir = Path(str(prepared_record["strategy_bundle"]))
    state_dir = tmp_path / "owner-state"
    owner_evidence = tmp_path / "owner-evidence"
    strategy_evidence = tmp_path / "strategy-evidence"

    owner_process = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT),
            "owner",
            "--bundle-dir",
            str(owner_dir),
            "--state-dir",
            str(state_dir),
            "--evidence-dir",
            str(owner_evidence),
            "--stop-after-submits",
            "2",
            "--max-runtime-seconds",
            "20",
        ],
        cwd=REPOSITORY,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
    )
    try:
        ready = _wait_for_started(owner_evidence / "owner-runtime.json", owner_process)
        assert ready["source_mode"] == "synthetic-only"

        duplicate = _run(
            "second-owner-check",
            "--bundle-dir",
            str(owner_dir),
            "--state-dir",
            str(state_dir),
            "--evidence-file",
            str(tmp_path / "second-owner.json"),
        )
        assert duplicate.returncode == 0, {
            "stdout": duplicate.stdout,
            "stderr": duplicate.stderr,
        }
        duplicate_record = _json_stdout(duplicate)
        assert duplicate_record["second_owner_rejected"] is True
        assert duplicate_record["error_code"] == "ORDER_OWNER_ALREADY_ACTIVE"
        assert duplicate_record["second_owner_provider_calls"] == 0

        probe = _run(
            "probe",
            "--bundle-dir",
            str(strategy_dir),
            "--evidence-dir",
            str(strategy_evidence),
            "--orders",
            "2",
            timeout=20,
        )
        assert probe.returncode == 0, {"stdout": probe.stdout, "stderr": probe.stderr}
        probe_record = _json_stdout(probe)
        assert probe_record["business_submit_attempts"] == 2
        assert probe_record["reconnects"] == 1
        assert probe_record["registration_count"] == 2
        assert len(set(probe_record["service_epochs"])) == 1
        assert [row["state"] for row in probe_record["business_results"]] == ["ACKED", "ACKED"]
        assert probe_record["direct_adapter_calls"] == 0
        assert probe_record["direct_fallback_configured"] is False
        assert probe_record["normal_client_shutdown"] is True

        owner_process.wait(timeout=8)
        stdout, stderr = owner_process.communicate(timeout=1)
        assert owner_process.returncode == 0, {"stdout": stdout, "stderr": stderr}
        final_owner = json.loads((owner_evidence / "owner-runtime.json").read_text(encoding="utf-8"))
        provider_calls = json.loads((owner_evidence / "provider-calls.json").read_text(encoding="utf-8"))
        assert final_owner["normal_shutdown"] is True
        assert final_owner["provider_submit_calls"] == 2
        assert provider_calls["provider_submit_calls"] == 2
        assert provider_calls["provider_cancel_calls"] == 0
        assert provider_calls["direct_adapter_calls"] == 0

        unavailable = _run(
            "probe",
            "--bundle-dir",
            str(strategy_dir),
            "--evidence-dir",
            str(tmp_path / "after-stop-evidence"),
            "--orders",
            "1",
            "--expect-unavailable",
            timeout=8,
        )
        assert unavailable.returncode == 0, {
            "stdout": unavailable.stdout,
            "stderr": unavailable.stderr,
        }
        unavailable_record = _json_stdout(unavailable)
        assert unavailable_record["expected_unavailable_observed"] is True
        assert unavailable_record["business_submit_attempts"] == 0
        assert unavailable_record["direct_adapter_calls"] == 0
        assert unavailable_record["direct_fallback_configured"] is False
    finally:
        if owner_process.poll() is None:
            owner_process.terminate()
            try:
                owner_process.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                owner_process.kill()
                owner_process.communicate(timeout=2)
