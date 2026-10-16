from __future__ import annotations

import signal
from pathlib import Path

import pytest

import bt_api_py.gateway_operator_cli as operator_cli
from bt_api_py.gateway_operator_application import GatewayOperatorApplicationError


class _LifecycleApp:
    def __init__(self, handlers: dict[int, object], *, start_state: str) -> None:
        self.handlers = handlers
        self.state = "new"
        self.start_state = start_state
        self.stop_calls = 0

    @property
    def status(self):
        return {
            "state": self.state,
            "role": "account_session_worker",
            "ready": self.state == "running",
            "error_code": None,
            "operation": {"name": None, "state": "idle", "error_code": None},
            "account_session": None,
            "cleanup": {"unclaimed_provider_count": 0, "last_cleanup_code": None},
        }

    def start(self, *, timeout_seconds: float):
        assert self.handlers, "CLI must install controls before starting the owner"
        if self.start_state == "uncertain":
            self.handlers[signal.SIGTERM](signal.SIGTERM, None)
            self.state = "uncertain"
            raise GatewayOperatorApplicationError("APPLICATION_START_TIMEOUT")
        if self.start_state == "running_then_uncertain":
            self.state = "running"
            self.handlers[signal.SIGTERM](signal.SIGTERM, None)
            return
        self.state = self.start_state

    def stop(self, *, timeout_seconds: float):
        self.stop_calls += 1
        if self.stop_calls == 1 and self.start_state == "running_then_uncertain":
            self.state = "uncertain"
            self.handlers[signal.SIGTERM](signal.SIGTERM, None)
            raise GatewayOperatorApplicationError("APPLICATION_STOP_TIMEOUT")
        self.state = "closed"
        return self.status


def _wire_cli(monkeypatch: pytest.MonkeyPatch, app: _LifecycleApp) -> dict[int, object]:
    handlers: dict[int, object] = app.handlers
    monkeypatch.setattr(
        operator_cli.signal,
        "getsignal",
        lambda _signum: None,
    )

    def install(signum: int, handler: object) -> None:
        handlers[signum] = handler

    monkeypatch.setattr(operator_cli.signal, "signal", install)
    monkeypatch.setattr(
        operator_cli,
        "load_gateway_operator_launch_config",
        lambda _path: object(),
    )
    monkeypatch.setattr(
        operator_cli.GatewayOperatorProfileRegistry,
        "from_entry_points",
        classmethod(lambda _cls: object()),
    )
    monkeypatch.setattr(
        operator_cli.GatewayOperatorApplication,
        "from_launch_config",
        classmethod(lambda _cls, _config, _profiles: app),
    )
    return handlers


def test_cli_keeps_startup_timeout_owner_reachable_until_confirmed_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _LifecycleApp({}, start_state="uncertain")
    _wire_cli(monkeypatch, app)

    assert operator_cli.main(["--config", str(tmp_path / "trusted-profile.yaml")]) == 1
    assert app.stop_calls == 1
    assert app.status["state"] == "closed"


def test_cli_retries_stop_after_uncertain_close_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _LifecycleApp({}, start_state="running_then_uncertain")
    _wire_cli(monkeypatch, app)

    assert operator_cli.main(["--config", str(tmp_path / "trusted-profile.yaml")]) == 1
    assert app.stop_calls == 2
    assert app.status["state"] == "closed"


def test_cli_reports_owner_failure_with_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _LifecycleApp({}, start_state="failed")
    _wire_cli(monkeypatch, app)

    assert operator_cli.main(["--config", str(tmp_path / "trusted-profile.yaml")]) == 1
    assert app.stop_calls == 0


@pytest.mark.parametrize(
    ("option", "value"),
    (
        ("--startup-timeout", "0"),
        ("--startup-timeout", "nan"),
        ("--startup-timeout", "301"),
        ("--operation-timeout", "-1"),
        ("--operation-timeout", "inf"),
        ("--operation-timeout", "301"),
    ),
)
def test_cli_rejects_invalid_timeouts_before_loading_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    option: str,
    value: str,
) -> None:
    def fail_if_loaded(_path: Path) -> object:
        pytest.fail("CLI must validate timeout arguments before loading the profile")

    monkeypatch.setattr(operator_cli, "load_gateway_operator_launch_config", fail_if_loaded)
    with pytest.raises(SystemExit) as exc_info:
        operator_cli.main(["--config", str(tmp_path / "trusted-profile.yaml"), option, value])
    assert exc_info.value.code == 2
