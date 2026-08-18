"""Legacy positional order API compatibility tests (Task 2.3)."""

from __future__ import annotations

import pytest

from bt_api_py import BtApi
from bt_api_py._contracts.errors import LegacyOrderApiError


def _api_with_spy_feed() -> tuple[BtApi, list[tuple]]:
    api = BtApi(None, debug=False)
    calls: list[tuple] = []

    class _SpyFeed:
        def make_order(self, *args, **kwargs):
            calls.append((args, kwargs))
            return "order-1"

    api.exchange_feeds["MOCK___SPOT"] = _SpyFeed()
    api.data_queues["MOCK___SPOT"] = object()
    return api, calls


def test_legacy_bare_limit_raises_before_feed_call() -> None:
    api, calls = _api_with_spy_feed()
    with pytest.raises(LegacyOrderApiError):
        api.make_order("MOCK___SPOT", "BTCUSDT", 0.001, 50000, "limit")
    assert calls == [], "feed must not be called for unresolvable legacy side"


def test_legacy_bare_market_raises_before_feed_call() -> None:
    api, calls = _api_with_spy_feed()
    with pytest.raises(LegacyOrderApiError):
        api.make_order("MOCK___SPOT", "BTCUSDT", 0.001, 0, "market")
    assert calls == [], "feed must not be called for unresolvable legacy side"


def test_legacy_buy_limit_maps_through_to_feed() -> None:
    api, calls = _api_with_spy_feed()
    result = api.make_order("MOCK___SPOT", "BTCUSDT", 0.001, 50000, "buy-limit")
    assert result == "order-1"
    assert len(calls) == 1
    args, _ = calls[0]
    assert args[3] == "buy-limit", "resolvable buy-limit must pass side-type to feed"


def test_legacy_sell_market_maps_through_to_feed() -> None:
    api, calls = _api_with_spy_feed()
    api.make_order("MOCK___SPOT", "BTCUSDT", 1, 0, "sell-market")
    assert len(calls) == 1
    args, _ = calls[0]
    assert args[3] == "sell-market"
