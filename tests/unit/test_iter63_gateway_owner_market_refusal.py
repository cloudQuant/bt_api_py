"""A provider's own refusal of a subscribe ends that acquisition only.

SimNow 2026-11-10: a strategy asked for an instrument the shared CTP market
session could not add again. The owner bridge treats every failed subscribe as
an unknown native outcome and retains its handle until the owner's cleanup, so
the Hub kept a release pending and answered MARKET_NOT_READY to every strategy
until the Gateway was restarted.
"""

import time

import pytest
from bt_api_base.market_protocol import MarketProviderEvent
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.market_hub import GatewayMarketRegistration, GatewayMarketService

from bt_api_py.gateway_owner_market import GatewayOwnerMarketError
from tests.unit.test_iter63_gateway_owner_market import (
    close_hub,
    make_hub,
    release_handle,
    wait_until,
)
from tests.unit.test_iter63_gateway_owner_market import system as system
from tests.unit.test_service_market import _KEY, _OTHER_KEY


class Refusal(RuntimeError):
    """A native provider's typed refusal: it holds no handle for the key."""

    subscription_refused = True


def _native(system):
    bridge, owner, holder = system
    owner.submit_service_call(lambda: bridge._native()).result(timeout_seconds=1)
    return bridge, holder["provider"]


def _refuse(native, monkeypatch, keys, error=Refusal):
    real = native.subscribe
    asked = []

    def subscribe(key, *, generation):
        asked.append(key)
        if key in keys:
            native._check("subscribe")
            raise error("refused")
        return real(key, generation=generation)

    monkeypatch.setattr(native, "subscribe", subscribe)
    return asked


def test_refused_acquisition_is_released_at_once_and_the_key_stays_usable(system, monkeypatch):
    bridge, native = _native(system)
    refused = {_KEY}
    asked = _refuse(native, monkeypatch, refused)
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_until(lambda: bridge._states[handle].pending.done)
    for _ in range(2):
        with pytest.raises(GatewayOwnerMarketError, match="MARKET_SUBSCRIBE_REFUSED"):
            bridge.subscription_ready(handle)
    assert bridge.unsubscribe(handle) is True  # nothing native to release
    assert handle not in bridge._states and "unsubscribe" not in native.calls
    assert bridge.unsubscribe(handle) is True  # idempotent
    # The synchronous form drops the handle itself.
    with pytest.raises(GatewayOwnerMarketError, match="MARKET_SUBSCRIBE_REFUSED"):
        bridge._timeout = 2
        bridge.subscribe(_KEY, generation="gen-2")
    assert not bridge._states
    # Once the provider accepts the key, the same bridge acquires it.
    refused.clear()
    again = bridge.begin_subscribe(_KEY, generation="gen-3")
    wait_until(lambda: bridge.subscription_ready(again))
    assert len(asked) == 3 and len(native.active) == 1
    assert release_handle(bridge, again)
    assert bridge.close()  # no owner cleanup was needed for the refusals


def test_an_unmarked_failure_is_still_an_unknown_outcome(system, monkeypatch):
    bridge, native = _native(system)
    _refuse(native, monkeypatch, {_KEY}, error=RuntimeError)
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_until(lambda: bridge._states[handle].pending.done)
    with pytest.raises(GatewayOwnerMarketError, match="SERVICE_CALL_FAILED"):
        bridge.subscription_ready(handle)
    assert bridge.unsubscribe(handle) is False and handle in bridge._states
    assert not bridge.close()


def test_marker_must_be_exactly_true(system, monkeypatch):
    class Loose(RuntimeError):
        subscription_refused = "yes"

    bridge, native = _native(system)
    _refuse(native, monkeypatch, {_KEY}, error=Loose)
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    wait_until(lambda: bridge._states[handle].pending.done)
    with pytest.raises(GatewayOwnerMarketError, match="SERVICE_CALL_FAILED"):
        bridge.subscription_ready(handle)
    assert bridge.unsubscribe(handle) is False


def _service(hub):
    scope = WireScope("paper:market-unit")
    registrations = {
        name: GatewayMarketRegistration(
            principal_ref="principal", execution_domain_ref=scope.execution_domain_ref
        )
        for name in ("r1", "r2")
    }
    service = GatewayMarketService(hub, registrations=registrations)

    def call(operation, registration, **arguments):
        return service.handle(
            "market." + operation, principal_ref="principal", scope=scope,
            registration_id=registration, arguments=arguments, result_fits=lambda result: True,
        )

    def begin(operation_id, key, registration):
        return call("bootstrap_begin", registration, operation_id=operation_id,
                    refresh_existing=False,
                    subscriptions=[{"key": key.to_dict(), "require_source_descriptor": False}])

    def settle(operation_id, registration):
        deadline = time.monotonic() + 3
        while True:
            hub.maintain(["r1", "r2"])
            result = call("bootstrap_status", registration, operation_id=operation_id)
            if result["status"] not in {"pending", "cancelling"} or time.monotonic() >= deadline:
                return result
            time.sleep(0.002)

    return begin, settle


def test_one_refused_key_does_not_take_the_market_plane_down(system, monkeypatch):
    bridge, native = _native(system)
    bridge._timeout = 2
    refused = {_OTHER_KEY}
    _refuse(native, monkeypatch, refused)
    published = []
    hub = make_hub(bridge, [_KEY, _OTHER_KEY], published)
    begin, settle = _service(hub)
    try:
        assert begin("1" * 32, _KEY, "r1")["status"] == "pending"
        assert settle("1" * 32, "r1")["status"] == "active"

        # Another strategy asks for a key the provider refuses.
        assert begin("2" * 32, _OTHER_KEY, "r2")["status"] == "pending"
        rejected = settle("2" * 32, "r2")
        assert rejected["status"] == "rejected" and rejected["code"] == "PROVIDER_SUBSCRIBE_FAILED"
        wait_until(lambda: hub.maintain(["r1", "r2"]) is not None and hub.ready)
        assert hub.readiness["pending_releases"] == 0 and hub.readiness["starting_streams"] == 0

        # The first strategy's stream keeps flowing.
        native.events.append(
            MarketProviderEvent(native.generation, "2026-01-01T00:00:00Z", {"value": 7})
        )
        wait_until(lambda: hub.maintain(["r1", "r2"]) is not None and published)
        assert published[0].payload["data"]["value"] == 7

        # When the provider accepts the key later, the same Hub serves it.
        refused.clear()
        assert begin("3" * 32, _OTHER_KEY, "r2")["status"] == "pending"
        assert settle("3" * 32, "r2")["status"] == "active"
        assert hub.registration_active(_OTHER_KEY, "r2") and hub.registration_active(_KEY, "r1")
    finally:
        close_hub(hub)


def test_release_asked_while_the_refused_subscribe_is_still_running(system, monkeypatch):
    import threading

    bridge, native = _native(system)
    _refuse(native, monkeypatch, {_KEY})
    release = threading.Event()
    native.gate = ("subscribe", release)
    handle = bridge.begin_subscribe(_KEY, generation="gen")
    assert native.entered.wait(1)
    assert bridge.unsubscribe(handle) is False  # the native call is in flight
    release.set()
    wait_until(lambda: bridge._states[handle].pending.done)
    # The very call that learns of the refusal releases the handle.
    assert bridge.unsubscribe(handle) is True
    assert handle not in bridge._states and "unsubscribe" not in native.calls
    assert bridge.close()
