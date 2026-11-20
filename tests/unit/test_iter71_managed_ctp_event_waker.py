from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from bt_api_py import bt_api as bt_api_module
from bt_api_py.bt_api import BtApi
from bt_api_py.event_batch_handoff import EventIngressQueue


_REPO_ROOT = Path(__file__).parents[2]
_CTP_ROOT = _REPO_ROOT / "bt_api" / "bt_api_ctp"


def _load_base_stream() -> ModuleType:
    source = _CTP_ROOT / "src" / "bt_api_ctp" / "feeds" / "base_stream.py"
    spec = importlib.util.spec_from_file_location(
        "iter71_managed_ctp_event_waker_base_stream", source
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SilentLogger:
    def warning(self, _message: str) -> None:
        return None


class _FakeFeed:
    def __init__(self) -> None:
        self.disconnected = False

    def disconnect(self) -> None:
        self.disconnected = True


def _new_api(monkeypatch) -> BtApi:
    monkeypatch.setattr(bt_api_module, "_plugins_loaded", True)
    api = BtApi(exchange_kwargs={}, debug=False)
    api.logger = _SilentLogger()
    api.log = lambda *_args, **_kwargs: None
    return api


def test_managed_ctp_trade_stream_wakes_retained_consumer_until_unregister(
    monkeypatch,
) -> None:
    base_stream = _load_base_stream()

    class _SyntheticCtpTradeStream(base_stream.BaseDataStream):
        def start(self) -> None:
            self._running = True

        def wait_connected(self, *, timeout: float) -> bool:
            assert timeout == 5.0
            return True

        def connect(self) -> None:
            return None

        def disconnect(self) -> None:
            return None

        def subscribe_topics(self, _topics) -> None:
            return None

        def _run_loop(self) -> None:
            return None

    monkeypatch.setattr(
        bt_api_module.ExchangeRegistry,
        "get_stream_class",
        lambda *_args, **_kwargs: _SyntheticCtpTradeStream,
    )

    api = _new_api(monkeypatch)
    exchange_name = "CTP___SYNTHETIC"
    source = EventIngressQueue()
    feed = _FakeFeed()
    api.exchange_kwargs[exchange_name] = {}
    api.data_queues[exchange_name] = source
    api.exchange_feeds[exchange_name] = feed
    monkeypatch.setattr(api, "_ctp_execution_stream_ready", lambda _name: True)

    observed_queue_sizes: list[int] = []
    token = api.register_event_waker(
        lambda: observed_queue_sizes.append(source.qsize())
    )

    api._prepare_ctp_execution_stream(exchange_name)
    assert len(api._subscription_streams) == 1
    stream = api._subscription_streams[0]
    assert stream.data_queue.target is source

    stream.push_data("before-close")
    assert source.get_nowait() == "before-close"

    api.close()
    assert api._event_waker_closed is True
    assert feed.disconnected is True

    # A provider callback already in flight may publish after close. Existing
    # consumers stay registered until they explicitly release their token.
    stream.push_data("late-after-close")
    assert source.get_nowait() == "late-after-close"

    assert api.unregister_event_waker(token) is True
    stream.push_data("after-unregister")
    assert source.get_nowait() == "after-unregister"

    # One wake follows each successful enqueue, and close contributes exactly
    # one terminal hint. No callback is delivered after token removal.
    assert observed_queue_sizes == [1, 0, 1]
