import queue
from unittest.mock import Mock

from bt_api_py.feeds.live_ib_web_feed import IbWebRequestDataStock


class _FakeClock:
    def __init__(self) -> None:
        self.current = 0.0

    def time(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += max(float(seconds), 0.01)


class TestIbWebRequestDataConnect:
    def test_connect_waits_until_authenticated(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr("bt_api_py.feeds.live_ib_web_feed.time.time", clock.time)
        monkeypatch.setattr("bt_api_py.feeds.live_ib_web_feed.time.sleep", clock.sleep)

        feed = IbWebRequestDataStock(queue.Queue(), timeout=1.0)
        feed._connect_poll_interval = 0.1
        feed.check_auth_status = Mock(
            side_effect=[
                {"authenticated": False},
                {"authenticated": False},
                {"authenticated": True},
            ]
        )
        feed.reauthenticate = Mock(return_value={"authenticated": False})

        feed.connect()

        assert feed.is_connected() is True
        assert feed.reauthenticate.call_count >= 1
        assert feed._last_session_check > 0

    def test_connect_stays_disconnected_when_auth_never_ready(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr("bt_api_py.feeds.live_ib_web_feed.time.time", clock.time)
        monkeypatch.setattr("bt_api_py.feeds.live_ib_web_feed.time.sleep", clock.sleep)

        feed = IbWebRequestDataStock(queue.Queue(), timeout=0.35)
        feed._connect_poll_interval = 0.1
        feed.check_auth_status = Mock(return_value={"authenticated": False})
        feed.reauthenticate = Mock(return_value={"authenticated": False})

        feed.connect()

        assert feed.is_connected() is False
        assert feed.reauthenticate.call_count >= 1
