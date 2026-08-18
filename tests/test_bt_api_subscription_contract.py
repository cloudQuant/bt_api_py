"""Subscription outcome contract tests (Task 1.3)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from bt_api_py import BtApi
from bt_api_py._contracts.errors import CapabilityNotSupportedError


def _make_api_with_exchange(exchange_name: str = "MOCK___SPOT") -> BtApi:
    api = BtApi(None, debug=False)
    api.exchange_feeds[exchange_name] = object()
    api.data_queues[exchange_name] = Mock()
    api.exchange_kwargs[exchange_name] = {}
    return api


def test_subscribe_without_handler_raises_capability_error(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api_with_exchange()
    from bt_api_base.registry import ExchangeRegistry

    monkeypatch.setattr(ExchangeRegistry, "get_stream_class", lambda *a, **k: None)

    with pytest.raises(CapabilityNotSupportedError):
        api.subscribe("MOCK___SPOT___BTCUSDT", [{"topic": "ticker"}])


def test_subscribe_bar_num_not_drifted_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api_with_exchange()
    from bt_api_base.registry import ExchangeRegistry

    monkeypatch.setattr(ExchangeRegistry, "get_stream_class", lambda *a, **k: None)

    assert api.subscribe_bar_num == 0
    with pytest.raises(CapabilityNotSupportedError):
        api.subscribe("MOCK___SPOT___BTCUSDT", [{"topic": "kline"}])
    assert api.subscribe_bar_num == 0, "failed subscription must not drift the bar count"


def test_subscribe_bar_num_increments_only_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _make_api_with_exchange()
    from bt_api_base.registry import ExchangeRegistry

    calls: list[bool] = []

    def _handler(data_queue, exchange_params, topics, owner) -> None:
        calls.append(True)

    monkeypatch.setattr(ExchangeRegistry, "get_stream_class", lambda *a, **k: _handler)

    api.subscribe("MOCK___SPOT___BTCUSDT", [{"topic": "kline"}])
    assert len(calls) == 1
    assert api.subscribe_bar_num == 1


def test_batch_query_reports_per_venue_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _make_api_with_exchange("GOOD___SPOT")
    api.exchange_feeds["BAD___SPOT"] = object()
    api.data_queues["BAD___SPOT"] = Mock()

    class _GoodFeed:
        def get_tick(self, symbol, *args, **kwargs):
            return {"price": 1.0}

    class _BadFeed:
        def get_tick(self, symbol, *args, **kwargs):
            raise RuntimeError("venue down")

    api.exchange_feeds["GOOD___SPOT"] = _GoodFeed()
    api.exchange_feeds["BAD___SPOT"] = _BadFeed()

    results = api.get_all_ticks("BTCUSDT")

    assert "GOOD___SPOT" in results
    assert "BAD___SPOT" in results, "failed venue must be reported, not silently omitted"
    assert isinstance(results["BAD___SPOT"], Exception)
