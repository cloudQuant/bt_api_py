"""Offline behavior contracts for the IBKR browser-cookie helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
COOKIE_SCRIPTS = (
    "scripts/get_ibkr_cookie.py",
    "scripts/tools/get_ibkr_cookie.py",
)


class BrowserCookie3Module(ModuleType):
    chrome: Mock
    firefox: Mock
    safari: Mock
    edge: Mock


class RequestsExceptionsModule(ModuleType):
    RequestException: type[Exception]


class RequestsModule(ModuleType):
    get: Mock
    exceptions: RequestsExceptionsModule


class OfflineRequestError(Exception):
    """Exception used to exercise the offline request-failure branch."""


@pytest.mark.parametrize("relative_path", COOKIE_SCRIPTS)
def test_browser_failure_is_logged_safely_and_next_browser_is_tried(
    relative_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_value = "session-cookie-secret"
    exception_text = f"failed while handling {cookie_value}"
    logger = Mock()

    browser_cookie3 = BrowserCookie3Module("browser_cookie3")
    browser_cookie3.chrome = Mock(side_effect=RuntimeError(exception_text))
    browser_cookie3.firefox = Mock(
        return_value=[SimpleNamespace(name="session", value=cookie_value)]
    )
    browser_cookie3.safari = Mock(
        return_value=[SimpleNamespace(name="session", value=cookie_value)]
    )
    browser_cookie3.edge = Mock()

    requests = RequestsModule("requests")
    requests.get = Mock(
        side_effect=[
            OfflineRequestError("offline request failure"),
            SimpleNamespace(status_code=200),
        ]
    )
    requests.exceptions = RequestsExceptionsModule("requests.exceptions")
    requests.exceptions.RequestException = OfflineRequestError

    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setitem(sys.modules, "browser_cookie3", browser_cookie3)
    monkeypatch.setitem(sys.modules, "requests", requests)

    script_path = ROOT / relative_path
    module_name = "isolated_" + relative_path.removesuffix(".py").replace("/", "_")
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "logger", logger, raising=False)

    assert module.get_cookie_from_browser() == {"session": cookie_value}
    assert browser_cookie3.chrome.call_args.kwargs == {"domain_name": "localhost"}
    assert browser_cookie3.firefox.call_args.kwargs == {"domain_name": "localhost"}
    assert browser_cookie3.safari.call_args.kwargs == {"domain_name": "localhost"}
    browser_cookie3.edge.assert_not_called()
    assert requests.get.call_count == 2

    logger.debug.assert_called_once()
    diagnostic_args = logger.debug.call_args.args
    assert diagnostic_args[-2:] == ("Chrome", "RuntimeError")
    diagnostic = " ".join(str(value) for value in diagnostic_args)
    assert cookie_value not in diagnostic
    assert exception_text not in diagnostic
