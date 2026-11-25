"""Iteration 73.06: Cooperative zero-byte owner stop-file parity for Gateway, Gateway Web, and Risk Web CLIs."""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from bt_api_py import gateway_operator_cli, gateway_web_local_host, risk_web_local_host


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _protect(path: Path) -> None:
    if os.name == "nt":
        from bt_api_py._win32_security import _set_owner_only_acl

        _set_owner_only_acl(path)
    else:
        os.chmod(path, 0o600 if path.is_file() else 0o700)


def _write_zero_byte_marker(path: Path) -> None:
    path.write_bytes(b"")
    _protect(path)


def test_gateway_operator_cli_owner_stop_file_exits_zero(tmp_path, monkeypatch, capsys):
    _protect(tmp_path)
    cfg_path = tmp_path / "gateway.yaml"
    cfg_path.write_text("profile_ref: synthetic\n", encoding="utf-8")
    _protect(cfg_path)

    events: list[str] = []

    class FakeApp:
        def __init__(self):
            self._state = "new"

        @property
        def status(self):
            return {"state": self._state, "cleanup": {"unclaimed_provider_count": 0, "last_cleanup_code": None}}

        def start(self, *, timeout_seconds):
            events.append("start")
            self._state = "running"

        def stop(self, *, timeout_seconds):
            events.append("stop")
            self._state = "closed"

    fake_app = FakeApp()
    monkeypatch.setattr(
        gateway_operator_cli,
        "load_gateway_operator_launch_config",
        lambda _p: SimpleNamespace(profile_ref="synthetic", service_config=None),
    )
    monkeypatch.setattr(
        gateway_operator_cli.GatewayOperatorProfileRegistry,
        "from_entry_points",
        staticmethod(lambda: object()),
    )
    monkeypatch.setattr(
        gateway_operator_cli.GatewayOperatorApplication,
        "from_launch_config",
        staticmethod(lambda _cfg, _profiles: fake_app),
    )

    rc_box: list[int] = []
    marker_box: list[Path] = []
    original_new_marker = None

    from bt_api_py import _owner_stop_file

    original_new_marker = _owner_stop_file.new_marker

    def capture_marker(directory: Path, prefix: str) -> Path:
        marker = original_new_marker(directory, prefix)
        marker_box.append(marker)
        return marker

    monkeypatch.setattr(_owner_stop_file, "new_marker", capture_marker)

    def run_cli():
        rc_box.append(
            gateway_operator_cli.main(
                ["--config", str(cfg_path), "--enable-owner-stop-file"]
            )
        )

    # Install signal handlers only works on main thread; stub signal.signal for worker-thread test.
    monkeypatch.setattr(gateway_operator_cli.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(gateway_operator_cli.signal, "getsignal", lambda *_a: None)

    worker = threading.Thread(target=run_cli, daemon=True)
    worker.start()
    deadline = time.monotonic() + 5.0
    while not marker_box and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(marker_box) == 1
    marker = marker_box[0]

    # A non-empty marker file must be ignored (fail-closed zero-byte policy).
    marker.write_bytes(b"non-empty")
    _protect(marker)
    time.sleep(0.25)
    assert worker.is_alive() and fake_app._state == "running"

    # Replacing with a protected zero-byte marker triggers clean cooperative shutdown.
    _write_zero_byte_marker(marker)
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert rc_box == [0]
    assert events == ["start", "stop"]
    out = capsys.readouterr().out
    assert f"GATEWAY_SERVICE_STOP_FILE {marker.name}" in out
    assert "GATEWAY_SERVICE_STOPPED" in out


@pytest.mark.parametrize(
    ("module", "host_attr", "expected_prefix", "expected_stopped"),
    [
        (
            gateway_web_local_host,
            "LocalGatewayWebHost",
            "GATEWAY_WEB_STOP_FILE",
            "GATEWAY_WEB_STOPPED",
        ),
        (
            risk_web_local_host,
            "LocalRiskWebHost",
            "RISK_WEB_STOP_FILE",
            "RISK_WEB_STOPPED",
        ),
    ],
)
def test_local_web_hosts_stop_cleanly_on_zero_byte_owner_stop_file(
    tmp_path, monkeypatch, capsys, module, host_attr, expected_prefix, expected_stopped
):
    from bt_api_py import _owner_stop_file

    _protect(tmp_path)
    cfg_path = tmp_path / "web.json"
    cfg_path.write_text("{}", encoding="utf-8")
    _protect(cfg_path)
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    lifecycle: list[str] = []

    class FakeWebHost:
        def __init__(self, *_args, **_kwargs):
            self.origin = origin
            self.app = FastAPI()

        def start(self):
            lifecycle.append("start")

        def close(self):
            lifecycle.append("close")

    monkeypatch.setattr(module, host_attr, FakeWebHost)
    monkeypatch.setattr(module.signal, "signal", lambda *_a: None)
    if module is gateway_web_local_host:
        import bt_api_gateway.web.app as gw_web_app

        monkeypatch.setattr(gw_web_app, "bundled_static_dir", lambda: tmp_path)

    marker_box: list[Path] = []
    original_new_marker = _owner_stop_file.new_marker

    def capture_marker(directory: Path, prefix: str) -> Path:
        marker = original_new_marker(directory, prefix)
        marker_box.append(marker)
        return marker

    monkeypatch.setattr(_owner_stop_file, "new_marker", capture_marker)

    def trigger_stop():
        deadline = time.monotonic() + 5.0
        while not marker_box and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker_box
        # Wait until the loopback port is actually listening before issuing stop marker.
        listen_deadline = time.monotonic() + 5.0
        while time.monotonic() < listen_deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        _write_zero_byte_marker(marker_box[0])

    trigger = threading.Thread(target=trigger_stop, daemon=True)
    trigger.start()
    module.main(["--config", str(cfg_path), "--enable-owner-stop-file"])
    trigger.join(timeout=5.0)
    assert lifecycle == ["start", "close"]
    out = capsys.readouterr().out
    assert f"{expected_prefix} {marker_box[0].name}" in out
    assert expected_stopped in out
