"""Isolated child-process probe for CTP/SWIG trader connections."""

from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
from pathlib import Path

# Subprocess support is needed only to isolate native CTP/SWIG cleanup for this fixed probe.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

_SIMNOW_PROBE_SCRIPT = """
import os
from bt_api_py.ctp.client import TraderClient

client = TraderClient(
    os.environ["BTAPI_SIMNOW_TD_FRONT"],
    os.environ["BTAPI_SIMNOW_BROKER_ID"],
    os.environ["BTAPI_SIMNOW_USER_ID"],
    os.environ["BTAPI_SIMNOW_PASSWORD"],
    app_id=os.environ["BTAPI_SIMNOW_APP_ID"],
    auth_code=os.environ["BTAPI_SIMNOW_AUTH_CODE"],
)
client.start(block=False)
ready = client.wait_ready(timeout=6)
print(f"{ready=}", flush=True)
client.stop()
os._exit(0 if ready else 1)
""".strip()
_SIMNOW_PROBE_COMMAND = (sys.executable, "-c", _SIMNOW_PROBE_SCRIPT)

_INPUT_ENV_KEYS = {
    "td_front": "BTAPI_SIMNOW_TD_FRONT",
    "broker_id": "BTAPI_SIMNOW_BROKER_ID",
    "user_id": "BTAPI_SIMNOW_USER_ID",
    "password": "BTAPI_SIMNOW_PASSWORD",
    "app_id": "BTAPI_SIMNOW_APP_ID",
    "auth_code": "BTAPI_SIMNOW_AUTH_CODE",
}
_SENSITIVE_INPUTS = ("broker_id", "user_id", "password", "app_id", "auth_code")


def _redact_sensitive_values(details: str, values: tuple[str, ...]) -> str:
    for value in sorted(values, key=len, reverse=True):
        details = details.replace(value, "[REDACTED]")
    return details


def probe_simnow_trader_connection(
    *,
    td_front: str,
    broker_id: str,
    user_id: str,
    password: str,
    app_id: str,
    auth_code: str,
) -> tuple[bool, str]:
    """Probe a Trader connection in an isolated process and return sanitized output."""
    inputs = {
        "td_front": td_front,
        "broker_id": broker_id,
        "user_id": user_id,
        "password": password,
        "app_id": app_id,
        "auth_code": auth_code,
    }
    missing = [
        name for name, value in inputs.items() if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ValueError(f"missing required SimNow probe input(s): {', '.join(missing)}")

    child_env = os.environ.copy()
    child_env.update(
        {env_key: inputs[input_name] for input_name, env_key in _INPUT_ENV_KEYS.items()}
    )

    # Command/script are module constants, caller values use fixed env keys, and shell is never used.
    # A child process is required to isolate native CTP/SWIG cleanup from the test runner.
    result = subprocess.run(  # noqa: S603  # nosec B603
        list(_SIMNOW_PROBE_COMMAND),
        cwd=_REPOSITORY_ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    details = f"{result.stdout or ''}{result.stderr or ''}".strip()
    sensitive_values = tuple(inputs[name] for name in _SENSITIVE_INPUTS)
    return result.returncode == 0, _redact_sensitive_values(details, sensitive_values)
