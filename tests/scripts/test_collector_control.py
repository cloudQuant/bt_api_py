"""Offline tests for the detached CTP collector controller."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ctp_data import collector_control as control

FAKE_SERVICE = textwrap.dedent(
    r"""
    import json
    import os
    import sys
    import time
    from pathlib import Path

    ready, stop, config, token, mode = sys.argv[1:]
    if mode in ("ready", "ignore-marker"):
        Path(ready).write_text(
            json.dumps({"pid": os.getpid(), "config": config, "started_at": "offline-test"}),
            encoding="utf-8",
        )
    deadline = time.monotonic() + 0.5 if mode in ("exit", "ignore-marker") else None
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            break
        try:
            marker = json.loads(Path(stop).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            marker = {}
        if marker.get("token") == token and mode != "ignore-marker":
            break
        time.sleep(0.01)
    """
)


def _configure_fake_service(monkeypatch, tmp_path: Path, *, mode: str = "ready") -> Path:
    config_path = tmp_path / "collector.yaml"
    config_path.write_text("data_root: offline\n", encoding="utf-8")
    monkeypatch.setattr(control, "SERVICE_LOG", tmp_path / "service.log")
    monkeypatch.setattr(control, "CONSOLE_LOG", tmp_path / "service-console.log")
    monkeypatch.setattr(control, "_preflight", lambda _config: None)

    def build_command(*, config, stopfile, readyfile, log_file, extra_args):
        del log_file
        if mode == "ready":
            assert extra_args == ["--lead", "123"]
        else:
            assert extra_args == []
        token = Path(stopfile).stem
        return [
            sys.executable,
            "-c",
            FAKE_SERVICE,
            str(readyfile),
            str(stopfile),
            str(config),
            token,
            mode,
        ]

    monkeypatch.setattr(control, "_build_service_command", build_command)
    return config_path


def test_start_status_stop_runs_offline_child_and_uses_token_marker(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config_path = _configure_fake_service(monkeypatch, tmp_path)
    runtime_dir = tmp_path / "runtime"

    result = control._start(
        config_path=config_path,
        runtime_dir=runtime_dir,
        startup_timeout=3,
        stop_timeout=3,
        service_args=["--lead", "123"],
    )
    assert result == control.EXIT_RUNNING
    state = json.loads((runtime_dir / control.STATE_NAME).read_text(encoding="utf-8"))
    assert state["pid"] > 0
    assert state["process_creation_identity"]
    assert state["startup_receipt"]["started_at"] == "offline-test"
    assert state["stopfile"] == str(runtime_dir / f"{state['token']}.stop")
    assert state["readyfile"] == str(runtime_dir / f"{state['token']}.ready")
    assert control._status(runtime_dir=runtime_dir) == control.EXIT_RUNNING
    status_output = capsys.readouterr().out
    assert f"PID {state['pid']}" in status_output
    assert "startup=offline-test" in status_output
    assert "stop requested: no" in status_output

    assert (
        control._start(
            config_path=config_path,
            runtime_dir=runtime_dir,
            startup_timeout=1,
            stop_timeout=1,
            service_args=["--lead", "123"],
        )
        == control.EXIT_RUNNING
    )
    assert control._stop(runtime_dir=runtime_dir, stop_timeout=3) == 0
    assert not (runtime_dir / control.STATE_NAME).exists()
    assert control._status(runtime_dir=runtime_dir) == control.EXIT_STOPPED
    assert "collector service is stopped" in capsys.readouterr().out


def test_start_failed_handshake_requests_cooperative_stop_and_clears_owned_state(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config_path = _configure_fake_service(monkeypatch, tmp_path, mode="no-ready")
    runtime_dir = tmp_path / "runtime"

    result = control._start(
        config_path=config_path,
        runtime_dir=runtime_dir,
        startup_timeout=0.15,
        stop_timeout=3,
        service_args=[],
    )
    assert result == control.EXIT_BAD_STATE
    assert not (runtime_dir / control.STATE_NAME).exists()
    output = capsys.readouterr().err
    assert "did not complete startup handshake" in output
    assert "service-console.log" in output


def test_stop_timeout_leaves_running_state_and_never_kills_child(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    config_path = _configure_fake_service(monkeypatch, tmp_path, mode="ignore-marker")
    runtime_dir = tmp_path / "runtime"
    assert (
        control._start(
            config_path=config_path,
            runtime_dir=runtime_dir,
            startup_timeout=2,
            stop_timeout=1,
            service_args=[],
        )
        == control.EXIT_RUNNING
    )
    state = json.loads((runtime_dir / control.STATE_NAME).read_text(encoding="utf-8"))

    assert control._stop(runtime_dir=runtime_dir, stop_timeout=0.05) == control.EXIT_STOP_TIMEOUT
    assert (runtime_dir / control.STATE_NAME).exists()
    assert control._process_matches(state["pid"], state["process_creation_identity"])
    assert "no process was killed" in capsys.readouterr().err

    deadline = time.monotonic() + 3
    while control._process_matches(state["pid"], state["process_creation_identity"]):
        if time.monotonic() >= deadline:
            pytest.fail("fake service did not exit on its own")
        time.sleep(0.02)
    assert control._status(runtime_dir=runtime_dir) == control.EXIT_STOPPED
    assert not (runtime_dir / control.STATE_NAME).exists()


def test_windows_hidden_launch_uses_detached_group_and_append_log(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        captured["stdout_name"] = kwargs["stdout"].name
        return sentinel

    monkeypatch.setattr(control.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(control.os, "name", "nt")
    monkeypatch.setattr(control.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(control.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    console_log = tmp_path / "service-console.log"

    assert (
        control._popen_hidden(["python", "service.py"], cwd=tmp_path, console_log=console_log)
        is sentinel
    )
    kwargs = captured["kwargs"]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["close_fds"] is True
    assert kwargs["creationflags"] == 0x08000200
    assert captured["stdout_name"] == str(console_log)


def test_status_clears_reused_pid_state_without_touching_that_process(
    tmp_path: Path, capsys
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    token = "a" * 32
    stopfile, readyfile = control._state_paths(runtime_dir.resolve(), token)
    state = {
        "pid": os.getpid(),
        "process_creation_identity": "different-process-start-time",
        "token": token,
        "stopfile": str(stopfile),
        "readyfile": str(readyfile),
        "config": str(tmp_path / "collector.yaml"),
    }
    stopfile.write_text(json.dumps({"token": token}), encoding="utf-8")
    control._atomic_write_json(runtime_dir / control.STATE_NAME, state)

    assert control._status(runtime_dir=runtime_dir) == control.EXIT_STOPPED
    assert os.getpid() > 0
    assert not (runtime_dir / control.STATE_NAME).exists()
    assert not stopfile.exists()
    assert "stale state cleared" in capsys.readouterr().out


def test_preflight_reports_missing_ctp_fields_without_showing_values(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "collector.yaml"
    config_path.write_text("data_root: offline\n", encoding="utf-8")
    for name in (
        "CTP_MD_FRONT",
        "CTP_TD_FRONT",
        "CTP_BROKER_ID",
        "CTP_USER_ID",
        "CTP_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    source = control.REPO_ROOT / "bt_api" / "bt_api_ctp" / "src"
    monkeypatch.setattr(sys, "path", [str(source), *sys.path])
    monkeypatch.setattr(control, "prepare_runtime", lambda: None)

    with pytest.raises(control.ControlError) as exc_info:
        control._preflight(config_path)
    message = str(exc_info.value)
    assert "CTP_MD_FRONT" in message
    assert "CTP_PASSWORD" in message
    assert "password" not in message.lower().replace("ctp_password", "")


def test_status_rejects_unknown_arguments() -> None:
    with pytest.raises(SystemExit) as exc_info:
        control.main(["status", "--lead", "60"])
    assert exc_info.value.code == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows command-line quoting")
def test_environment_service_args_select_config_and_are_forwarded_once(
    tmp_path: Path, monkeypatch
) -> None:
    selected_config = tmp_path / "config with spaces.yaml"
    selected_config.write_text("data_root: offline\n", encoding="utf-8")
    monkeypatch.setenv(
        "COLLECTOR_SERVICE_ARGS",
        subprocess.list2cmdline(["--retry", "7", "--config", str(selected_config)]),
    )
    captured: dict[str, object] = {}

    def fake_start(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(control, "_start", fake_start)
    result = control.main(["start", "--runtime-dir", str(tmp_path / "runtime"), "--lead", "60"])

    assert result == 0
    assert captured["config_path"] == selected_config.resolve()
    assert captured["service_args"] == [
        "--retry",
        "7",
        "--config",
        str(selected_config),
        "--lead",
        "60",
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows command-line quoting")
def test_explicit_controller_config_overrides_environment_config(
    tmp_path: Path, monkeypatch
) -> None:
    env_config = tmp_path / "env config.yaml"
    cli_config = tmp_path / "cli config.yaml"
    monkeypatch.setenv(
        "COLLECTOR_SERVICE_ARGS",
        subprocess.list2cmdline(["--retry", "7", "--config", str(env_config)]),
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(control, "_start", lambda **kwargs: captured.update(kwargs) or 0)

    assert (
        control.main(
            [
                "start",
                "--config",
                str(cli_config),
                "--runtime-dir",
                str(tmp_path / "runtime"),
            ]
        )
        == 0
    )
    assert captured["config_path"] == cli_config.resolve()
    assert captured["service_args"] == ["--retry", "7"]
