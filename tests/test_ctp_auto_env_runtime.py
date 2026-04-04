from __future__ import annotations

import atexit
import contextlib
import os
import queue
import socket
from pathlib import Path

import pytest
from dotenv import load_dotenv

from bt_api_py.ctp._ctp_base import get_ctp_import_error, is_ctp_native_loaded
from bt_api_py.ctp_env_selector import apply_ctp_env

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

_CTP_ATEXIT_REGISTERED = False

if not is_ctp_native_loaded():
    _ctp_err = get_ctp_import_error()
    pytest.exit(
        f"CTP C++ extension (_ctp) not available: {_ctp_err}. Run: git lfs install && git lfs pull",
        returncode=1,
    )


def _ctp_atexit_handler() -> None:
    os._exit(0)


def _ensure_ctp_atexit() -> None:
    global _CTP_ATEXIT_REGISTERED
    if not _CTP_ATEXIT_REGISTERED:
        atexit.register(_ctp_atexit_handler)
        _CTP_ATEXIT_REGISTERED = True


def _check_ctp_service(front: str, timeout: float = 3.0) -> str:
    host, port_text = front.replace("tcp://", "").split(":")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, int(port_text)))
        try:
            data = sock.recv(128)
            return "ok" if data else "no_service"
        except TimeoutError:
            return "no_service"
        finally:
            sock.close()
    except TimeoutError:
        return "timeout"
    except ConnectionRefusedError:
        return "refused"
    except Exception as exc:
        return f"error:{exc}"


def _runtime_config() -> tuple[dict[str, str], str, str, str]:
    td_front, md_front, env_name = apply_ctp_env()
    broker_id = os.environ.get("CTP_BROKER_ID") or os.environ.get("SIMNOW_BROKER_ID") or "9999"
    user_id = os.environ.get("CTP_USER_ID") or os.environ.get("SIMNOW_USER_ID") or ""
    password = os.environ.get("CTP_PASSWORD") or os.environ.get("SIMNOW_PASSWORD") or ""
    app_id = os.environ.get("CTP_APP_ID", "simnow_client_test")
    auth_code = os.environ.get("CTP_AUTH_CODE", "0000000000000000")
    if not user_id or not password:
        pytest.skip("CTP_USER_ID/CTP_PASSWORD or SIMNOW_USER_ID/SIMNOW_PASSWORD not configured")
    config = {
        "broker_id": broker_id,
        "user_id": user_id,
        "password": password,
        "app_id": app_id,
        "auth_code": auth_code,
    }
    return config, td_front, md_front, env_name


@pytest.mark.network
@pytest.mark.ctp
def test_ctp_request_feed_uses_auto_env_fronts_and_connects() -> None:
    _ensure_ctp_atexit()
    from bt_api_py.feeds.live_ctp_feed import CtpRequestDataFuture

    config, expected_td, expected_md, env_name = _runtime_config()
    svc_status = _check_ctp_service(expected_td)
    if svc_status == "refused":
        pytest.skip(f"CTP front {expected_td} refused connection (env={env_name})")
    if svc_status == "timeout" or svc_status.startswith("error:"):
        pytest.skip(f"CTP front {expected_td} unreachable: {svc_status}")

    feed = CtpRequestDataFuture(queue.Queue(), connect_timeout=20, **config)
    assert feed.td_front == expected_td
    assert feed.md_front == expected_md
    assert feed.ctp_env_name == env_name

    try:
        feed.connect()
        assert feed._connected, f"CtpRequestDataFuture failed to connect via auto env {env_name}"
        assert feed.trader_client is not None
        assert feed.trader_client.is_ready
    finally:
        with contextlib.suppress(Exception):
            feed.disconnect()


@pytest.mark.network
@pytest.mark.ctp
def test_btapi_ctp_feed_uses_auto_env_fronts_and_connects() -> None:
    _ensure_ctp_atexit()
    from bt_api_py.bt_api import BtApi

    config, expected_td, expected_md, env_name = _runtime_config()
    svc_status = _check_ctp_service(expected_td)
    if svc_status == "refused":
        pytest.skip(f"CTP front {expected_td} refused connection (env={env_name})")
    if svc_status == "timeout" or svc_status.startswith("error:"):
        pytest.skip(f"CTP front {expected_td} unreachable: {svc_status}")

    api = BtApi({"CTP___FUTURE": dict(config)}, debug=True)
    feed = api.get_request_api("CTP___FUTURE")
    assert feed is not None
    assert feed.td_front == expected_td
    assert feed.md_front == expected_md
    assert feed.ctp_env_name == env_name

    try:
        feed.connect()
        assert feed._connected, f"BtApi CTP feed failed to connect via auto env {env_name}"
        assert feed.trader_client is not None
        assert feed.trader_client.is_ready
    finally:
        with contextlib.suppress(Exception):
            feed.disconnect()
