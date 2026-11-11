"""The owner-side market adapter hands every drained batch to an observer (strategy cash, D63-55)."""
import logging
import threading
from decimal import Decimal

import pytest
from bt_api_base.market_protocol import MarketProviderEvent

from bt_api_py.ctp_gateway_strategy_financial import OwnerLastPrices
from bt_api_py.gateway_owner_market import GatewayOwnerMarketProvider
from tests.unit.test_iter63_gateway_owner_market import (
    _KEY,
    Harness,
    Native,
    owner_for,
    poll_after_ready,
    release_handle,
    wait_until,
)


@pytest.fixture
def build(tmp_path):
    made = []

    def make(observer):
        owner = owner_for(Harness(tmp_path).factory)
        assert owner.start(2)
        holder = {}

        def getter():
            if "provider" not in holder:
                holder["provider"] = Native()
            return holder["provider"]

        bridge = GatewayOwnerMarketProvider(owner, getter, call_timeout_seconds=0.03, event_observer=observer)
        made.append((bridge, owner))
        return bridge, holder

    yield make
    for bridge, owner in made:
        bridge.close()
        assert owner.close(2)
        assert bridge.close()


def _drain(bridge, handle, *, limit=4):
    assert poll_after_ready(bridge, handle, limit=limit) == ()
    wait_until(lambda: bridge._states[handle].pending.done)
    return poll_after_ready(bridge, handle, limit=limit)


def _tick(price, symbol="rb2701"):
    return MarketProviderEvent("gen", "2026-01-01T00:00:00Z", {
        "schema": "sdk.market.tick", "instrument": {"provider": "ctp", "native_symbol": symbol},
        "last_price": price})


def test_observer_gets_each_drained_batch_on_the_owner_thread(build):
    seen = []
    bridge, holder = build(lambda events: seen.append((threading.get_ident(), events)))
    handle = bridge.subscribe(_KEY, generation="gen")
    native = holder["provider"]
    assert _drain(bridge, handle) == () and seen == []          # nothing drained, nothing observed
    native.events.extend([_tick(3001.0), _tick(3002.0)])
    batch = _drain(bridge, handle)
    assert len(batch) == 2
    assert seen == [(native.thread, batch)] and native.thread != threading.get_ident()
    assert release_handle(bridge, handle)


def test_last_prices_follow_the_market_through_the_adapter(build):
    prices = OwnerLastPrices()
    bridge, holder = build(prices.observe)
    handle = bridge.subscribe(_KEY, generation="gen")
    holder["provider"].events.extend([_tick(3001.0), _tick(3002.5), _tick(0.0)])
    assert len(_drain(bridge, handle)) == 3
    assert prices.last("rb2701")[0] == Decimal("3002.5") and prices.last("hc2701") is None
    assert release_handle(bridge, handle)


def test_a_failing_observer_never_reaches_the_subscribers(build, caplog):
    calls = []

    def observer(events):
        calls.append(len(events))
        raise RuntimeError("observer bug")

    caplog.set_level(logging.WARNING, logger="bt_api_py.gateway_owner_market")
    bridge, holder = build(observer)
    handle = bridge.subscribe(_KEY, generation="gen")
    for price in (3001.0, 3002.0):
        holder["provider"].events.append(_tick(price))
        batch = _drain(bridge, handle)
        assert len(batch) == 1 and batch[0].data["last_price"] == price
    assert calls == [1, 1]
    assert [record.getMessage() for record in caplog.records] == [
        "Market event observer failed (reported once): RuntimeError"]
    assert release_handle(bridge, handle)


def test_observer_must_be_callable(tmp_path):
    owner = owner_for(Harness(tmp_path).factory)
    with pytest.raises(ValueError, match="INVALID_OWNER_MARKET_CONFIGURATION"):
        GatewayOwnerMarketProvider(owner, lambda: None, event_observer="observe")
