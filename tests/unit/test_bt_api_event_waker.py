from __future__ import annotations

import importlib.util
import queue
import sys
import threading
from pathlib import Path
from types import ModuleType

import pytest

from bt_api_py import bt_api as bt_api_module
from bt_api_py.bt_api import BtApi

_REPO_ROOT = Path(__file__).parents[2]
_CTP_ROOT = _REPO_ROOT / "bt_api" / "bt_api_ctp"


def _load_base_stream() -> ModuleType:
    source = _CTP_ROOT / "src" / "bt_api_ctp" / "feeds" / "base_stream.py"
    spec = importlib.util.spec_from_file_location("ac50_test_ctp_base_stream", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_ctp_plugin_with_fake_native_leaf(monkeypatch) -> tuple[ModuleType, type]:
    base_stream = _load_base_stream()

    class FakeCtpStream(base_stream.BaseDataStream):
        def start(self) -> None:
            self._running = True

        def connect(self) -> None:
            return None

        def disconnect(self) -> None:
            self.disconnected = True

        def subscribe_topics(self, topics) -> None:
            return None

        def _run_loop(self) -> None:
            return None

    package = ModuleType("bt_api_ctp")
    package.__path__ = []
    package.__version__ = "ac50-test"
    exchange_data = ModuleType("bt_api_ctp.exchange_data")
    exchange_data.CtpExchangeDataFuture = type("CtpExchangeDataFuture", (), {})
    feeds = ModuleType("bt_api_ctp.feeds")
    feeds.__path__ = []
    live_feed = ModuleType("bt_api_ctp.feeds.live_ctp_feed")
    live_feed.CtpMarketStream = FakeCtpStream
    live_feed.CtpRequestDataFuture = type("CtpRequestDataFuture", (), {})
    live_feed.CtpTradeStream = FakeCtpStream
    for name, module in (
        ("bt_api_ctp", package),
        ("bt_api_ctp.exchange_data", exchange_data),
        ("bt_api_ctp.feeds", feeds),
        ("bt_api_ctp.feeds.live_ctp_feed", live_feed),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    source = _CTP_ROOT / "src" / "bt_api_ctp" / "plugin.py"
    spec = importlib.util.spec_from_file_location("ac50_test_ctp_plugin", source)
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin, FakeCtpStream


class _SilentLogger:
    def __init__(self) -> None:
        self.warnings = []

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def _new_api(monkeypatch) -> BtApi:
    monkeypatch.setattr(bt_api_module, "_plugins_loaded", True)
    api = BtApi(exchange_kwargs={}, debug=False)
    api.logger = _SilentLogger()
    api.log = lambda *_args, **_kwargs: None
    return api


def test_real_ctp_plugin_binding_wakes_same_btapi_after_enqueue(monkeypatch) -> None:
    plugin, _stream_type = _load_ctp_plugin_with_fake_native_leaf(monkeypatch)
    api = _new_api(monkeypatch)
    data_queue = queue.Queue()
    wake_observations = []
    token = api.register_event_waker(lambda: wake_observations.append(data_queue.qsize()))

    plugin._ctp_future_subscribe_handler(
        data_queue,
        {"subscribe_account": True},
        [{"topic": "tick", "symbol": "IF2612"}],
        api,
    )

    assert len(api._subscription_streams) == 2
    for index, stream in enumerate(api._subscription_streams, start=1):
        assert stream._event_waker.__self__ is api
        assert stream._event_waker.__func__ is BtApi._notify_event_wakers
        stream.push_data(f"event-{index}")
        assert wake_observations == list(range(1, index + 1))
    assert [data_queue.get_nowait(), data_queue.get_nowait()] == ["event-1", "event-2"]

    assert api.unregister_event_waker(token) is True
    assert api.unregister_event_waker(token) is False
    api._subscription_streams[0].push_data("after-unbind")
    assert wake_observations == [1, 2]


def test_close_wakes_once_after_streams_are_terminal(monkeypatch) -> None:
    plugin, _stream_type = _load_ctp_plugin_with_fake_native_leaf(monkeypatch)
    api = _new_api(monkeypatch)
    data_queue = queue.Queue()
    wake_observations = []
    token = api.register_event_waker(
        lambda: wake_observations.append(
            (api._event_waker_closed, all(stream.disconnected for stream in api._subscription_streams))
        )
    )
    plugin._ctp_future_subscribe_handler(
        data_queue,
        {"subscribe_account": True},
        [{"topic": "tick", "symbol": "IF2612"}],
        api,
    )

    api.close()
    api.close()

    assert wake_observations == [(True, True)]
    assert api.unregister_event_waker(token) is False
    with pytest.raises(RuntimeError, match="client_closed"):
        api.register_event_waker(lambda: None)


def test_close_failure_does_not_claim_terminal_wake(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    wake_count = []
    token = api.register_event_waker(lambda: wake_count.append(True))

    def fail_close() -> None:
        raise RuntimeError("synthetic close failure")

    api._close_transports = fail_close
    with pytest.raises(RuntimeError, match="synthetic close failure"):
        api.close()

    assert wake_count == []
    assert api._event_waker_closed is False
    assert api.unregister_event_waker(token) is True


def test_registration_is_bounded_callbacks_are_isolated_and_unbind_is_exact(
    monkeypatch,
) -> None:
    api = _new_api(monkeypatch)
    tokens = [api.register_event_waker(lambda: None) for _ in range(32)]
    with pytest.raises(RuntimeError, match="capacity_exceeded"):
        api.register_event_waker(lambda: None)
    assert all(api.unregister_event_waker(token) for token in tokens[:2])

    calls = []

    def broken_callback() -> None:
        raise RuntimeError("synthetic callback failure")

    broken_token = api.register_event_waker(broken_callback)
    good_token = api.register_event_waker(lambda: calls.append("good"))
    api._notify_event_wakers()

    assert calls == ["good"]
    assert api._event_waker_callback_errors == 1
    assert len(api.logger.warnings) == 1
    assert api.unregister_event_waker(object()) is False
    assert api.unregister_event_waker(broken_token) is True
    assert api.unregister_event_waker(good_token) is True
    assert all(api.unregister_event_waker(token) for token in tokens[2:])
    api._notify_event_wakers()
    assert calls == ["good"]


def test_unbind_waits_for_inflight_wake_and_prevents_later_dispatch(monkeypatch) -> None:
    api = _new_api(monkeypatch)
    callback_entered = threading.Event()
    release_callback = threading.Event()
    unbind_started = threading.Event()
    unbind_finished = threading.Event()
    results = []
    calls = []

    def callback() -> None:
        calls.append("wake")
        callback_entered.set()
        if not release_callback.wait(timeout=2):
            raise RuntimeError("test callback release timed out")

    token = api.register_event_waker(callback)
    dispatcher = threading.Thread(target=api._notify_event_wakers, daemon=True)
    dispatcher.start()
    assert callback_entered.wait(timeout=1)

    def unbind() -> None:
        unbind_started.set()
        results.append(api.unregister_event_waker(token))
        unbind_finished.set()

    unbinder = threading.Thread(target=unbind, daemon=True)
    unbinder.start()
    assert unbind_started.wait(timeout=1)
    assert not unbind_finished.wait(timeout=0.05)
    release_callback.set()
    assert unbind_finished.wait(timeout=1)
    dispatcher.join(timeout=1)
    unbinder.join(timeout=1)

    api._notify_event_wakers()
    assert results == [True]
    assert calls == ["wake"]
