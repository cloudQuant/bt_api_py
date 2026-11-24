"""INTEGRATION_SIM: one synthetic strategy through the separate Runtime CLI on Windows."""

import http.client
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from backtrader.live_state import initialize_identity
from bt_api_py._win32_security import _set_owner_only_acl
from bt_api_runtime.control_ledger import ControlDatabase
from bt_api_runtime.supervision import SystemClock


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Runtime owner lifecycle proof")

_TOKEN = "synthetic-runtime-owner-token-0123456789"
_UNIT = "synthetic"


_CHILD = r'''"""Harmless synthetic run using the production BT stop/status adapters."""
import argparse
import json
import os
import time
from pathlib import Path

from backtrader.live_trading.graceful_stop import stop_on_terminate
from backtrader.live_trading.run_status import RunStatusWriter


class _SyntheticLoop:
    _run_active = True

    def runstop(self):
        self._run_active = False


def _write(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-uuid", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--stop-seen-file", required=True)
    parser.add_argument("--stop-request-file", default=None)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--heartbeat-interval", type=float, required=True)
    args = parser.parse_args()

    loop = _SyntheticLoop()
    termination = stop_on_terminate(
        loop,
        stop_request_file=args.stop_request_file,
        stop_request_strategy_uuid=args.strategy_uuid,
        stop_request_run_id=args.run_id,
    )
    status = RunStatusWriter(
        os.getcwd(),
        run_id=args.run_id,
        heartbeat_interval_s=args.heartbeat_interval,
        strategy_uuid=args.strategy_uuid,
        stop_request_provider=termination.request_state,
    )
    status.set_phase("LIVE")
    status.start()
    with termination:
        _write(args.ready_file, {"pid": os.getpid(), "run_id": args.run_id})
        deadline = time.monotonic() + 30
        while loop._run_active:
            if time.monotonic() >= deadline:
                raise RuntimeError("synthetic child did not receive a Runtime stop")
            time.sleep(0.01)

    conclusion = status.finish(
        broker_summary={"status": "STOPPED", "unresolved_working_orders": 0}
    )
    if conclusion.summary_written is not True or conclusion.exit_code != 0:
        return 3
    _write(
        args.stop_seen_file,
        {"source": termination.source, "run_id": args.run_id},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_startup_line(process, timeout=20):
    ready = queue.Queue(maxsize=1)

    def read_line():
        ready.put(process.stdout.readline())

    threading.Thread(target=read_line, daemon=True).start()
    try:
        return ready.get(timeout=timeout).strip()
    except queue.Empty as exc:
        raise AssertionError("Runtime CLI did not report startup") from exc


def _wait_path(path, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError("synthetic child readiness timed out")


def _control(port, operation, *, token, unit=None, command_id=None, reason=""):
    payload = {
        "schema": "bt_api_run_contract.control/v1",
        "operation": operation,
        "unit": unit,
        "parameters": {},
        "command_id": command_id,
        "reason": reason,
        "confirm": None,
        "all_units": False,
    }
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(
            "POST",
            "/v1/control",
            body=json.dumps(payload),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token,
                "Host": "127.0.0.1:{}".format(port),
            },
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def _wait_unit_state(port, token, expected, timeout=10):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        status, payload = _control(port, "runtime.unit.status", token=token, unit=_UNIT)
        assert status == 200
        last = payload["unit"]
        if last["state"] == expected:
            return last
        time.sleep(0.05)
    raise AssertionError("unit did not reach {} (last state {})".format(
        expected, None if last is None else last["state"]
    ))


def test_sop_cli_windows_owner_runs_one_synthetic_unit_and_preserves_unknown(tmp_path):
    strategy_dir = tmp_path / "strategy"
    strategy_dir.mkdir()
    identity = initialize_identity(strategy_dir)
    entry = strategy_dir / "run.py"
    entry.write_text(_CHILD, encoding="utf-8")
    ready_file = tmp_path / "child-ready.json"
    stop_seen_file = tmp_path / "child-stop-seen.json"
    home = tmp_path / "runtime-home"
    port = _free_port()

    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(
        json.dumps(
            {
                "schema": "bt_api_runtime/v1",
                "home": str(home),
                "api": {
                    "transport": "http_json",
                    "bind": "127.0.0.1",
                    "port": port,
                    "identity_mechanism": "subject_token",
                },
                "operators": [{"name": "alice", "role": "operator", "units": [_UNIT]}],
                "monitor_events": {"enabled": False},
                "units": [
                    {
                        "name": _UNIT,
                        "kind": "strategy",
                        "dir": str(strategy_dir),
                        "entry": entry.name,
                        "python": sys.executable,
                        "args": [
                            "--strategy-uuid",
                            identity.strategy_uuid,
                            "--ready-file",
                            str(ready_file),
                            "--stop-seen-file",
                            str(stop_seen_file),
                        ],
                        "access": "direct",
                        "heartbeat_interval_s": 0.5,
                        "start_timeout_s": 10,
                        "stop": {"graceful_timeout_s": 5, "kill_timeout_s": 1},
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    token_path = tmp_path / "tokens.json"
    token_path.write_text(
        json.dumps(
            {
                "schema": "bt_api_py.runtime_subject_tokens.v1",
                "tokens": [{"token": _TOKEN, "operator": "alice"}],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _set_owner_only_acl(token_path)
    operator_path = tmp_path / "operator.json"
    operator_path.write_text(
        json.dumps(
            {
                "schema": "bt_api_py.runtime_operator.v1",
                "runtime_config_file": str(runtime_path),
                "subject_token_file": str(token_path),
                "gateway": None,
                "monitor": None,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    # Seed the same durable PENDING intent as a Runtime crash before dispatch.
    # The separate CLI owner must turn it UNKNOWN and must not launch it on replay.
    unknown_id = str(uuid.uuid4())
    database = ControlDatabase(str(home), SystemClock())
    try:
        pending = database.ledger.begin(
            unknown_id,
            operator="alice",
            client="cli",
            action="start",
            unit_name=_UNIT,
            unit_uuid=identity.strategy_uuid,
            unit_dir=str(strategy_dir),
            parameters={},
            reason="synthetic crash before claim",
            confirmation_required=False,
            confirmation_consumed=False,
        )
        assert pending.kind == "new"
    finally:
        database.close()

    owner = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "bt_api_py.runtime_operator_cli",
            "run",
            "--config",
            str(operator_path),
            "--enable-stop-file",
            "--enable-owner-stop-file",
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_pid = None
    owner_stop_path = None
    output = error = None
    started_id = str(uuid.uuid4())
    stopped_id = str(uuid.uuid4())
    try:
        line = _wait_startup_line(owner)
        assert line == "runtime_control_listening 127.0.0.1 {}".format(port), line
        stop_line = _wait_startup_line(owner)
        prefix = "runtime_control_stop_file "
        assert stop_line.startswith(prefix), stop_line
        stop_name = stop_line[len(prefix):]
        assert re.fullmatch(r"runtime-[0-9a-f]{32}\.stop", stop_name), stop_line
        owner_stop_path = home / stop_name
        assert owner_stop_path.parent == home

        status, unknown = _control(
            port,
            "runtime.commands.get",
            token=_TOKEN,
            command_id=unknown_id,
        )
        assert status == 200
        assert unknown["command"]["state"] == "UNKNOWN"
        assert unknown["command"]["code"] == "dispatch_result_unknown"
        status, replay = _control(
            port,
            "runtime.unit.start",
            token=_TOKEN,
            unit=_UNIT,
            command_id=unknown_id,
            reason="synthetic crash before claim",
        )
        assert status == 200 and replay["status"] == "UNKNOWN"
        assert not ready_file.exists(), "UNKNOWN command replay launched a child"

        status, initial = _control(port, "runtime.unit.status", token=_TOKEN, unit=_UNIT)
        assert status == 200 and initial["unit"]["identity_state"] == "present"
        assert initial["unit"]["process_alive"] is False

        status, started = _control(
            port,
            "runtime.unit.start",
            token=_TOKEN,
            unit=_UNIT,
            command_id=started_id,
            reason="synthetic lifecycle integration",
        )
        assert status == 200 and started["status"] == "APPLIED", started
        _wait_path(ready_file)
        child = json.loads(ready_file.read_text(encoding="utf-8"))
        child_pid = int(child["pid"])
        running = _wait_unit_state(port, _TOKEN, "RUNNING")
        assert running["heartbeat_fresh"] is True
        assert running["process_alive"] is True
        assert running["phase"] == "LIVE"

        status, stopped = _control(
            port,
            "runtime.unit.stop",
            token=_TOKEN,
            unit=_UNIT,
            command_id=stopped_id,
            reason="synthetic graceful stop",
        )
        assert status == 200 and stopped["status"] == "APPLIED", stopped
        assert stopped["state"] == "STOPPED"
        terminal = _wait_unit_state(port, _TOKEN, "STOPPED")
        assert terminal["clean"] is True
        assert terminal["process_alive"] is False
        assert json.loads(stop_seen_file.read_text(encoding="utf-8")) == {
            "source": "stop_file",
            "run_id": child["run_id"],
        }
        status, stop_record = _control(
            port,
            "runtime.commands.get",
            token=_TOKEN,
            command_id=stopped_id,
        )
        assert status == 200 and stop_record["command"]["state"] == "APPLIED"

        # An invalid, nonempty owner marker is retained and must not close the
        # Runtime listener.  Remove only this test-created marker before
        # issuing the valid zero-byte owner stop request at the same name.
        with owner_stop_path.open("xb") as marker:
            marker.write(b"not-empty")
        _set_owner_only_acl(owner_stop_path)
        time.sleep(0.45)
        assert owner.poll() is None
        status, still_stopped = _control(
            port, "runtime.unit.status", token=_TOKEN, unit=_UNIT
        )
        assert status == 200 and still_stopped["unit"]["state"] == "STOPPED"
        assert owner_stop_path.is_file() and not owner_stop_path.is_symlink()
        assert owner_stop_path.read_bytes() == b"not-empty"

        owner_stop_path.unlink()
        with owner_stop_path.open("xb"):
            pass
        _set_owner_only_acl(owner_stop_path)
        output, error = owner.communicate(timeout=10)
        assert owner.returncode == 0, (output, error)
        assert "runtime_control_closed" in output.splitlines(), output
        assert error == "", error
    finally:
        # Normal stop is the primary cleanup path.  Only if an assertion aborts
        # while the synthetic child is still present do we terminate that exact
        # temp fixture process so the test cannot leak it into the host.
        if owner.poll() is None and child_pid is not None and not stop_seen_file.exists():
            try:
                _control(
                    port,
                    "runtime.unit.stop",
                    token=_TOKEN,
                    unit=_UNIT,
                    command_id=str(uuid.uuid4()),
                    reason="synthetic test cleanup",
                )
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not stop_seen_file.exists():
                    time.sleep(0.05)
            except (OSError, http.client.HTTPException):
                pass
        if owner.poll() is None and child_pid is not None and not stop_seen_file.exists():
            try:
                import psutil
            except ImportError:
                pass
            else:
                try:
                    process = psutil.Process(child_pid)
                    command = process.cmdline()
                    if (
                        process.ppid() == owner.pid
                        and str(entry).casefold() in {value.casefold() for value in command}
                        and child["run_id"] in command
                    ):
                        process.kill()
                        process.wait(timeout=5)
                except (psutil.Error, OSError):
                    pass
        # Prefer the owner stop marker for bounded cleanup when it was issued.
        # This fallback is limited to this test's temp Runtime home and marker.
        if owner.poll() is None and owner_stop_path is not None:
            try:
                if not owner_stop_path.is_symlink():
                    if owner_stop_path.exists():
                        owner_stop_path.unlink()
                    with owner_stop_path.open("xb"):
                        pass
                    _set_owner_only_acl(owner_stop_path)
            except OSError:
                pass
        if output is None or error is None:
            try:
                output, error = owner.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                if owner.poll() is None:
                    owner.terminate()
                try:
                    output, error = owner.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    if owner.poll() is None:
                        owner.kill()
                    output, error = owner.communicate(timeout=5)
        assert _TOKEN not in output + error
