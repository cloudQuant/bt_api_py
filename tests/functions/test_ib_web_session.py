import queue

from bt_api_py.feeds.live_ib_web_feed import IbWebRequestDataStock
from bt_api_py.functions.ib_web_session import load_ib_web_settings


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
