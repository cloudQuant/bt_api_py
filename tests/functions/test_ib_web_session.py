from __future__ import annotations

import queue

from bt_api_py.exceptions import RequestFailedError
from bt_api_py.feeds.live_ib_web_feed import IbWebRequestDataStock
from bt_api_py.functions.ib_web_session import _click_mode, load_ib_web_settings


def test_load_ib_web_settings_resolves_relative_cookie_paths(tmp_path):
    settings = load_ib_web_settings(
        overrides={
            "cookie_source": "file:configs/ibkr_cookies.json",
            "cookie_output": "configs/ibkr_cookies.json",
        },
        base_dir=tmp_path,
    )

    expected = (tmp_path / "configs" / "ibkr_cookies.json").resolve()
    assert settings["cookie_source"] == f"file:{expected}"
    assert settings["cookie_output"] == str(expected)
    assert settings["cookie_output_relative"] == "configs/ibkr_cookies.json"


def test_ib_web_feed_connect_initializes_session_when_cookie_auth_fails(monkeypatch, tmp_path):
    cookie_path = (tmp_path / "configs" / "ibkr_cookies.json").resolve()

    def fake_ensure_authenticated_session(overrides=None, base_dir=None, env_file=None):
        return {
            "cookies": {"session": "fresh"},
            "cookie_output": str(cookie_path),
            "cookie_output_relative": "configs/ibkr_cookies.json",
            "cookie_source": f"file:{cookie_path}",
            "account_id": "DU999999",
            "status_code": 200,
            "used_login": True,
        }

    monkeypatch.setattr(
        "bt_api_py.feeds.live_ib_web_feed.ensure_authenticated_session",
        fake_ensure_authenticated_session,
    )

    feed = IbWebRequestDataStock(
        queue.Queue(),
        base_url="https://localhost:5000/v1/api",
        account_id="DU000000",
        verify_ssl=False,
        timeout=10,
        cookie_source="file:configs/ibkr_cookies.json",
        cookie_output="configs/ibkr_cookies.json",
        cookie_base_dir=str(tmp_path),
        username="user",
        password="pass",
        allow_browser_login=True,
    )

    auth_calls = {"count": 0}

    def fake_check_auth_status():
        auth_calls["count"] += 1
        if auth_calls["count"] == 1:
            raise RuntimeError("auth failed")
        return {"authenticated": True, "connected": True}

    feed.check_auth_status = fake_check_auth_status
    feed.reauthenticate = lambda: {"status": "ok"}

    feed.connect()

    assert feed.is_connected() is True
    assert feed.get_cookies() == {"session": "fresh"}
    assert feed.account_id == "DU999999"
    assert feed.get_last_connect_error() is None


def test_click_mode_returns_false_when_target_mode_already_selected():
    class _Switch:
        def count(self):
            return 1

        @property
        def first(self):
            return self

        def is_checked(self):
            return True

        def check(self, timeout=2000, force=True):
            raise AssertionError("should not check when already in target mode")

        def uncheck(self, timeout=2000, force=True):
            raise AssertionError("should not uncheck when already in target mode")

    class _Page:
        def locator(self, selector):
            assert selector == "input[name='paperSwitch']"
            return _Switch()

    assert _click_mode(_Page(), "paper") is False


def test_ib_web_feed_retries_with_http_after_local_ssl_eof(monkeypatch):
    class _DummyHttpClient:
        calls: list[str] = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def request(self, method, url, **kwargs):
            _DummyHttpClient.calls.append(url)
            if len(_DummyHttpClient.calls) == 1:
                raise RequestFailedError(
                    venue="IB_WEB",
                    message="Connection error: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1006)",
                )
            return {"authenticated": True, "connected": True}

        def close(self):
            return None

    monkeypatch.setattr(
        "bt_api_py.feeds.live_ib_web_feed.HttpClient",
        _DummyHttpClient,
    )

    feed = IbWebRequestDataStock(
        queue.Queue(),
        base_url="https://localhost:5000/v1/api",
        account_id="DU000000",
        verify_ssl=False,
        timeout=10,
        proxies={},
    )

    result = feed.check_auth_status()

    assert result == {"authenticated": True, "connected": True}
    assert _DummyHttpClient.calls == [
        "https://localhost:5000/v1/api/iserver/auth/status",
        "http://localhost:5000/v1/api/iserver/auth/status",
    ]
    assert feed.base_url == "http://localhost:5000/v1/api"
