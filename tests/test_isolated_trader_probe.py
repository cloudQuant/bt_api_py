"""Behavioral tests for the isolated CTP/SWIG probe wrapper."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from bt_api_py import _ctp_probe

TEST_INPUTS = {
    "td_front": "tcp://127.0.0.1:12345",
    "broker_id": "broker-sensitive-01",
    "user_id": "user-sensitive-02",
    "password": "password-sensitive-03",
    "app_id": "app-sensitive-04",
    "auth_code": "auth-sensitive-05",
}


def _probe(**overrides: str) -> tuple[bool, str]:
    inputs = {**TEST_INPUTS, **overrides}
    return _ctp_probe.probe_simnow_trader_connection(**inputs)


def test_probe_uses_static_command_fixed_cwd_and_allowlisted_values(monkeypatch) -> None:
    inherited_key = "CTP_PROBE_TEST_INHERITED_VALUE"
    monkeypatch.setenv(inherited_key, "preserved")
    for key in tuple(os.environ):
        if key.startswith("BTAPI_SIMNOW_"):
            monkeypatch.delenv(key)
    inherited_environment = os.environ.copy()
    call: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> object:
        call["command"] = command
        call["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "ready=True\n", "")

    monkeypatch.setattr(_ctp_probe.subprocess, "run", fake_run)

    ready, details = _probe()

    assert ready is True
    assert details == "ready=True"
    assert (
        sys.executable,
        "-c",
        _ctp_probe._SIMNOW_PROBE_SCRIPT,
    ) == _ctp_probe._SIMNOW_PROBE_COMMAND
    assert call["command"] == list(_ctp_probe._SIMNOW_PROBE_COMMAND)
    kwargs = call["kwargs"]
    assert isinstance(kwargs, dict)
    assert set(kwargs) == {"cwd", "env", "capture_output", "text", "timeout", "check"}
    assert kwargs["cwd"] == Path(_ctp_probe.__file__).resolve().parents[1]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 12
    assert kwargs["check"] is False
    assert "shell" not in kwargs

    child_environment = kwargs["env"]
    assert isinstance(child_environment, dict)
    expected_keys = {
        "BTAPI_SIMNOW_TD_FRONT": "td_front",
        "BTAPI_SIMNOW_BROKER_ID": "broker_id",
        "BTAPI_SIMNOW_USER_ID": "user_id",
        "BTAPI_SIMNOW_PASSWORD": "password",
        "BTAPI_SIMNOW_APP_ID": "app_id",
        "BTAPI_SIMNOW_AUTH_CODE": "auth_code",
    }
    assert {key: child_environment[key] for key in expected_keys} == {
        key: TEST_INPUTS[input_name] for key, input_name in expected_keys.items()
    }
    assert set(child_environment) == set(inherited_environment) | set(expected_keys)
    assert child_environment[inherited_key] == "preserved"


def test_probe_redacts_every_sensitive_input_from_child_output(monkeypatch) -> None:
    sensitive_values = [TEST_INPUTS[name] for name in _ctp_probe._SENSITIVE_INPUTS]
    output = "child stdout: " + " | ".join(sensitive_values)
    error = "child stderr: " + " | ".join(reversed(sensitive_values))

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, output, error)

    monkeypatch.setattr(_ctp_probe.subprocess, "run", fake_run)

    ready, details = _probe()

    assert ready is False
    assert "child stdout:" in details
    assert "child stderr:" in details
    assert all(value not in details for value in sensitive_values)
    assert details.count("[REDACTED]") == 2 * len(sensitive_values)


@pytest.mark.parametrize("field", tuple(TEST_INPUTS))
def test_probe_rejects_missing_inputs_before_subprocess(monkeypatch, field: str) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> object:
        pytest.fail("subprocess.run must not be called with a missing probe input")

    monkeypatch.setattr(_ctp_probe.subprocess, "run", fail_if_called)

    with pytest.raises(ValueError) as error:
        _probe(**{field: ""})

    message = str(error.value)
    assert field in message
    assert all(value not in message for value in TEST_INPUTS.values())
